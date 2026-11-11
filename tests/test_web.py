"""Prueba de integración del servidor, los trabajadores y la base de datos."""

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
    def iniciar(self, base):
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
        if proceso.poll() is None:
            proceso.send_signal(signal.SIGINT)
        proceso.wait(timeout=10)

    def peticion(self, ruta, datos=None):
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
        with TemporaryDirectory() as directorio:
            base = Path(directorio) / "prueba.sqlite3"
            proceso = self.iniciar(base)
            estado, _, inicio = self.peticion("/")
            self.assertEqual(estado, 200)
            self.assertIn("Arequipa → Puno", inicio)
            token = re.search(r'name="token" value="([^"]+)"', inicio).group(1)
            datos = {"token": token, "bus": 1, "asiento": 3}

            canales = ("Web", "Ventanilla 1", "Ventanilla 2")
            with ThreadPoolExecutor(max_workers=6) as clientes:
                resultados = list(clientes.map(
                    lambda canal: self.peticion("/reservar", dict(datos, canal=canal)),
                    canales * 2,
                ))
            self.assertEqual(sorted(estado for estado, _, _ in resultados), [303] + [409] * 5)
            ruta = next(ruta for estado, ruta, _ in resultados if estado == 303)
            codigo = ruta.split("=", 1)[1]
            gestion = {"token": token, "codigo": codigo}
            self.assertEqual(
                self.peticion("/reservar", dict(datos, bus=2, canal="Web"))[0], 303
            )
            self.assertIn("Pendiente de pago", self.peticion(ruta)[2])
            self.assertEqual(self.peticion("/pagar", gestion)[0], 303)
            self.assertEqual(self.peticion("/pagar", gestion)[0], 303)
            self.assertIn("Pagada", self.peticion(ruta)[2])
            self.assertEqual(self.peticion("/cancelar", gestion)[0], 303)
            self.assertEqual(self.peticion("/cancelar", gestion)[0], 303)
            self.assertEqual(self.peticion("/pagar", gestion)[0], 409)
            nueva = self.peticion("/reservar", dict(datos, canal="Web"))
            self.assertEqual(nueva[0], 303)
            self.assertNotEqual(nueva[1], ruta)
            self.detener(proceso)

            proceso = self.iniciar(base)
            self.assertIn("Cancelada con reembolso simulado", self.peticion(ruta)[2])
            self.assertIn("Pendiente de pago", self.peticion(nueva[1])[2])
            self.detener(proceso)


if __name__ == "__main__":
    unittest.main()
