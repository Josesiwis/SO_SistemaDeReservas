"""
Prueba de integración y estrés de concurrencia para el sistema de reservas.
Valida la prevención empírica de condiciones de carrera, exclusión mutua y persistencia.
"""

from concurrent.futures import ThreadPoolExecutor
from http.client import HTTPConnection
from pathlib import Path
import re
import signal
import subprocess
import sys
from tempfile import TemporaryDirectory
import unittest
from urllib.parse import urlencode


class FlujoReservas(unittest.TestCase):
    """Batería de pruebas de estrés y ciclo de vida de transacciones concurrentes."""
    
    def iniciar(self, base):
        """
        Inicia un proceso servidor independiente sobre una base temporal.
        - --puerto 0: El sistema operativo asigna dinámicamente un puerto libre efímero.
        - Captura la salida estándar para sincronizarse con el mensaje de arranque.
        """
        proceso = subprocess.Popen(
            [sys.executable, "-m", "sistema_reservas.web", "--db", str(base), "--puerto", "0"],
            stdout=subprocess.PIPE, text=True,
        )
        self.addCleanup(proceso.stdout.close)
        self.addCleanup(self.detener, proceso)
        linea = proceso.stdout.readline()
        self.assertTrue(linea.startswith("Reservas de pasajes: "), linea)
        self.puerto = int(linea.strip().rsplit(":", 1)[1])
        return proceso

    @staticmethod
    def detener(proceso):
        """Envía señal SIGINT al servidor y espera su terminación limpia con timeout."""
        if proceso.poll() is None:
            proceso.send_signal(signal.SIGINT)
        proceso.wait(timeout=10)

    def peticion(self, ruta, datos=None):
        """Envía una petición HTTP (GET o POST) al servidor de pruebas y retorna estado y cabeceras."""
        conexion = HTTPConnection("127.0.0.1", self.puerto, timeout=10)
        try:
            if datos is None:
                conexion.request("GET", ruta)
            else:
                conexion.request("POST", ruta, urlencode(datos), {
                    "Content-Type": "application/x-www-form-urlencoded",
                })
            respuesta = conexion.getresponse()
            return respuesta.status, respuesta.getheader("Location"), respuesta.read().decode()
        finally:
            conexion.close()

    def test_competencia_pago_cancelacion_y_persistencia(self):
        """
        PRUEBA INTEGRAL DE SISTEMAS OPERATIVOS:
        1. CONDICIÓN DE CARRERA: 6 hilos concurrentes compiten simultáneamente
           por el Asiento 3 del Bus 1 a través de los tres canales.
        2. EXCLUSIÓN MUTUA: Exactamente 1 cliente debe ganar (HTTP 303 Redirect)
           y exactamente 5 clientes deben ser rechazados (HTTP 409 Conflicto).
        3. CICLO DE VIDA: Prueba de idempotencia de pago y cancelación.
        4. LIBERACIÓN DEL RECURSO: Tras cancelar, el asiento vuelve a admitir una reserva nueva.
        5. PERSISTENCIA Y RECUPERACIÓN: Se apaga el servidor (SIGINT), se vuelve a iniciar
           y se valida que los boletos anteriores sigan registrados en disco.
        """
        with TemporaryDirectory() as directorio:
            base = Path(directorio) / "prueba.sqlite3"
            proceso = self.iniciar(base)
            
            # Verificación del catálogo inicial
            estado, _, inicio = self.peticion("/")
            self.assertEqual(estado, 200)
            self.assertIn("Arequipa → Puno", inicio)
            token = re.search(r'name="token" value="([^"]+)"', inicio).group(1)
            datos = {"token": token, "bus": 1, "asiento": 3}

            # Simulación de ráfaga concurrente con 6 clientes simultáneos
            canales = ("Web", "Ventanilla 1", "Ventanilla 2")
            with ThreadPoolExecutor(max_workers=6) as clientes:
                resultados = list(clientes.map(
                    lambda canal: self.peticion("/reservar", dict(datos, canal=canal)),
                    canales * 2,
                ))
            
            # Verificación estricta: 1 ganador (303) y 5 conflictos (409)
            self.assertEqual(sorted(estado for estado, _, _ in resultados), [303] + [409] * 5)
            ruta = next(ruta for estado, ruta, _ in resultados if estado == 303)
            codigo = ruta.split("=", 1)[1]
            gestion = {"token": token, "codigo": codigo}
            
            # Reserva en otro bus sin colisión
            self.assertEqual(
                self.peticion("/reservar", dict(datos, bus=2, canal="Web"))[0], 303
            )
            self.assertIn("Pendiente de pago", self.peticion(ruta)[2])
            
            # Pago simulado e idempotencia (pagar dos veces no rompe el estado)
            self.assertEqual(self.peticion("/pagar", gestion)[0], 303)
            self.assertEqual(self.peticion("/pagar", gestion)[0], 303)
            self.assertIn("Pagada", self.peticion(ruta)[2])
            
            # Cancelación simulada e idempotencia
            self.assertEqual(self.peticion("/cancelar", gestion)[0], 303)
            self.assertEqual(self.peticion("/cancelar", gestion)[0], 303)
            self.assertEqual(self.peticion("/pagar", gestion)[0], 409)
            
            # Re-reserva del asiento recién liberado
            nueva = self.peticion("/reservar", dict(datos, canal="Web"))
            self.assertEqual(nueva[0], 303)
            self.assertNotEqual(nueva[1], ruta)
            self.detener(proceso)

            # Prueba de persistencia tras reinicio del proceso servidor
            proceso = self.iniciar(base)
            self.assertIn("Cancelada con reembolso simulado", self.peticion(ruta)[2])
            self.assertIn("Pendiente de pago", self.peticion(nueva[1])[2])
            self.detener(proceso)


if __name__ == "__main__":
    unittest.main()
