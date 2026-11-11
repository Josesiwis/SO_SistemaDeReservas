"""
Interfaz web local y despacho concurrente de solicitudes a procesos trabajadores.
Implementa el patrón Productor-Consumidor y control de admisión mediante semáforos.
"""

import argparse
from concurrent.futures import ProcessPoolExecutor
from concurrent.futures.process import BrokenProcessPool
from html import escape
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import multiprocessing
from pathlib import Path
import secrets
import sqlite3
from threading import BoundedSemaphore
from urllib.parse import parse_qs, urlsplit

from .inventario import (
    CANALES, OperacionInvalida, aplicar_operacion, consultar_buses,
    consultar_reserva, inicializar,
)

ESTILOS = """
body{margin:0;background:#f4f6f9;color:#18243b;font:1rem/1.5 system-ui,sans-serif}
main{max-width:66rem;margin:auto;padding:2rem 1rem}
h1,h2,p{margin:0 0 .8rem}a{color:#174da0}
.grid{display:grid;grid-template-columns:repeat(auto-fit,minmax(min(100%,18rem),1fr));gap:1rem}
article,.panel{background:#fff;border:1px solid #d8e1ee;border-radius:.8rem;padding:1rem;margin-bottom:1rem}
form{display:flex;gap:.6rem;align-items:end;flex-wrap:wrap;margin-top:1rem}
label{display:grid;gap:.25rem}
input,select,button{font:inherit;border:1px solid #8998ae;border-radius:.4rem;padding:.45rem .65rem;max-width:100%;box-sizing:border-box}
button{background:#174da0;border-color:#174da0;color:white;cursor:pointer}
button.cancelar{background:#fff;color:#9c2435;border-color:#9c2435}
.aviso{border-left:4px solid #9c2435;padding:.55rem .8rem;background:#fff0f1}
.asientos{display:grid;grid-template-columns:repeat(5,1fr);gap:.3rem;margin:.7rem 0}
.asiento{text-align:center;border-radius:.3rem;background:#e0f4ea;color:#144b39;padding:.3rem}
.ocupado{background:#e9ebf0;color:#4b5566;text-decoration:line-through}
code{overflow-wrap:anywhere}small{color:#4b5566}
"""


def precio(centimos):
    """Convierte un importe entero en céntimos a representación formateada en Soles."""
    soles, resto = divmod(centimos, 100)
    return f"S/ {soles}.{resto:02d}"


class Servidor(ThreadingHTTPServer):
    """
    Servidor HTTP multihilo integrado con un grupo de procesos trabajadores.
    
    ARQUITECTURA DE SISTEMAS OPERATIVOS:
    - ThreadingHTTPServer: Atiende conexiones de red entrantes en hilos independientes.
    - BoundedSemaphore(trabajadores + 16): Mecanismo de Control de Admisión (Backpressure).
      Limita el número máximo de solicitudes de escritura activas y en espera.
      Evita que ráfagas masivas agoten los descriptores de archivo o la memoria del sistema.
    - ProcessPoolExecutor: Grupo de procesos del SO (consumidores) creados con 'spawn'.
      El contexto 'spawn' arranca procesos limpios e independientes, evitando problemas
      de estado compartido o bloqueos derivados del fork() en entornos multihilo.
    """
    def __init__(self, puerto, ruta, trabajadores):
        super().__init__(("127.0.0.1", puerto), Pagina)
        self.ruta = ruta
        self.token = secrets.token_urlsafe(32)
        # Semáforo de capacidad acotada: número de trabajadores + cola máxima de 16
        self.cupos = BoundedSemaphore(trabajadores + 16)
        self.procesos = ProcessPoolExecutor(
            max_workers=trabajadores, mp_context=multiprocessing.get_context("spawn")
        )


class Pagina(BaseHTTPRequestHandler):
    """Manejador de solicitudes HTTP para los canales Web, Ventanilla 1 y Ventanilla 2."""
    
    def parse_request(self):
        """Valida que la solicitud HTTP apunte únicamente a la interfaz local autorizada."""
        if not super().parse_request():
            return False
        puerto = self.server.server_port
        if self.headers.get("Host") not in (f"127.0.0.1:{puerto}", f"localhost:{puerto}"):
            self.send_error(400, "Host invalido")
            return False
        return True

    def pagina(self, contenido, estado=200):
        """Genera y transmite una página HTML completa con cabeceras de seguridad y no-cache."""
        html = f"""<!doctype html>
<html lang="es"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>Reservas de pasajes</title><style>{ESTILOS}</style></head>
<body><main>{contenido}</main></body></html>""".encode("utf-8")
        self.send_response(estado)
        self.send_header("Content-Type", "text/html; charset=utf-8")
        self.send_header("Content-Length", str(len(html)))
        self.send_header("Cache-Control", "no-store")
        self.send_header("Referrer-Policy", "no-referrer")
        self.end_headers()
        self.wfile.write(html)

    def error(self, mensaje, estado):
        """Renderiza una vista estándar de error con enlace de retorno."""
        self.pagina(
            f'<h1>Reservas de pasajes</h1><p class="aviso">{escape(mensaje)}</p>'
            '<p><a href="/">Volver a los buses</a></p>', estado
        )

    def campo_token(self):
        """Genera el campo oculto con token de protección contra falsificación de solicitudes (CSRF)."""
        return f'<input type="hidden" name="token" value="{self.server.token}">'

    def inicio(self):
        """Vista principal (GET /): renderiza el estado de los buses y el mapa de asientos libres/ocupados."""
        tarjetas = []
        canales = "".join(f"<option>{canal}</option>" for canal in CANALES)
        for bus in consultar_buses(self.server.ruta):
            numeros = range(1, bus["asientos"] + 1)
            ocupados = bus["ocupados"]
            libres = [numero for numero in numeros if numero not in ocupados]
            mapa = "".join(
                f'<span class="asiento{" ocupado" if n in ocupados else ""}" '
                f'aria-label="Asiento {n} {"ocupado" if n in ocupados else "libre"}">{n}</span>'
                for n in numeros
            )
            opciones = "".join(f"<option>{n}</option>" for n in libres)
            formulario = f"""
                <form method="post" action="/reservar">{self.campo_token()}
                <input type="hidden" name="bus" value="{bus['id']}">
                <label>Asiento<select name="asiento">{opciones}</select></label>
                <label>Canal<select name="canal">{canales}</select></label>
                <button>Reservar</button></form>""" if libres else ""
            tarjetas.append(f"""<article><h2>{escape(bus['ruta'])}</h2>
                <p>{precio(bus['tarifa_centimos'])} · {len(libres)} disponibles</p>
                <div class="asientos">{mapa}</div>{formulario}</article>""")
        self.pagina(f"""<h1>Reservas de pasajes</h1>
            <p>Elige un bus y un asiento. Los pagos son simulados.</p>
            <div class="grid">{''.join(tarjetas)}</div>
            <section class="panel"><h2>Consultar reserva</h2>
            <form action="/reserva" method="get">
            <label>Código<input name="codigo" maxlength="22" required></label>
            <button>Consultar</button></form></section>""")

    def detalle(self, codigo):
        """Vista de detalle (GET /reserva?codigo=...): muestra el estado del boleto y acciones disponibles."""
        reserva = consultar_reserva(self.server.ruta, codigo)
        if reserva is None:
            self.error("No encontramos una reserva con ese código.", 404)
            return
        acciones = []
        if reserva["cancelada"]:
            estado = "Cancelada con reembolso simulado" if reserva["pagada"] else "Cancelada"
        else:
            estado = "Pagada" if reserva["pagada"] else "Pendiente de pago"
            operaciones = [("cancelar", "Cancelar reserva")]
            if not reserva["pagada"]:
                operaciones.insert(0, ("pagar", "Simular pago"))
            for ruta, titulo in operaciones:
                acciones.append(f"""<form action="/{ruta}" method="post">
                    {self.campo_token()}<input type="hidden" name="codigo" value="{escape(codigo)}">
                    <button class="{ruta}">{titulo}</button></form>""")
        self.pagina(f"""<p><a href="/">Volver a los buses</a></p>
            <article><h1>{escape(reserva['ruta'])}</h1>
            <p><strong>{estado}</strong></p>
            <p>Asiento {reserva['asiento']} · {escape(reserva['canal'])}
            · {precio(reserva['importe_centimos'])}</p>
            <p>Código <code>{escape(codigo)}</code></p>
            <small>Guarda el código para consultar o gestionar la reserva.</small>
            {''.join(acciones)}</article>""")

    def do_GET(self):
        """Enrutador de peticiones de lectura (Lectores en el modelo de concurrencia)."""
        destino = urlsplit(self.path)
        try:
            if destino.path == "/":
                self.inicio()
            elif destino.path == "/reserva":
                codigo = parse_qs(destino.query).get("codigo", [""])[0].strip()
                self.detalle(codigo)
            else:
                self.error("Página no encontrada.", 404)
        except sqlite3.Error:
            self.error("No se pudo consultar el inventario.", 503)

    def do_POST(self):
        """
        Enrutador y despachador de peticiones de escritura (reservar, pagar, cancelar).
        
        FLUJO DE CONTROL DE CONCURRENCIA:
        1. Validación sintáctica y de seguridad (token CSRF, límites de tamaño).
        2. Control de admisión con semáforo:
           - Si no hay cupo disponible (acquire blocking=False), responde HTTP 503 de inmediato.
           - Si hay cupo, adquiere el recurso y continúa.
        3. Despacho asíncrono al pool de procesos trabajadores (submit -> result).
        4. Liberación garantizada del cupo del semáforo en bloque 'finally'.
        5. Patrón Post-Redirect-Get (PRG): Redirección HTTP 303 hacia la vista de detalle
           para evitar reenvíos accidentales por recarga del navegador.
        """
        operacion = self.path.removeprefix("/")
        if operacion not in ("reservar", "pagar", "cancelar"):
            self.error("Página no encontrada.", 404)
            return
        try:
            longitud = int(self.headers.get("Content-Length", "0"))
            if not 0 < longitud <= 2048:
                raise ValueError
            campos = parse_qs(self.rfile.read(longitud).decode(), max_num_fields=8)
            token = campos["token"][0]
            if not token.isascii() or not secrets.compare_digest(token, self.server.token):
                self.error("Vuelve a cargar la página antes de continuar.", 403)
                return
            if operacion == "reservar":
                bus, asiento = (campos[nombre][0] for nombre in ("bus", "asiento"))
                if any(not valor.isascii() or not valor.isdecimal() or len(valor) > 9
                       for valor in (bus, asiento)):
                    raise ValueError
                argumentos = (int(bus), int(asiento), campos["canal"][0])
            else:
                argumentos = (campos["codigo"][0],)
        except (ValueError, KeyError):
            self.error("La solicitud contiene datos inválidos.", 400)
            return

        # Control de admisión con semáforo (rechazo no bloqueante ante sobrecarga)
        if not self.server.cupos.acquire(blocking=False):
            self.error("Hay varias solicitudes en curso. Intenta nuevamente.", 503)
            return

        try:
            # Delegación de la transacción al proceso trabajador
            trabajo = self.server.procesos.submit(
                aplicar_operacion, self.server.ruta, operacion, *argumentos
            )
            codigo = trabajo.result()
        except OperacionInvalida as error:
            # Conflicto de negocio o condición de carrera perdida: HTTP 409
            self.error(str(error), 409)
            return
        except (sqlite3.Error, BrokenProcessPool):
            self.error("No se pudo procesar la solicitud. Consulta el estado antes de reintentar.", 503)
            return
        finally:
            # Liberación estricta del semáforo para admitir nuevas peticiones
            self.server.cupos.release()

        # Respuesta Post-Redirect-Get (HTTP 303 See Other)
        self.send_response(303)
        self.send_header("Location", f"/reserva?codigo={codigo}")
        self.send_header("Cache-Control", "no-store")
        self.end_headers()

    def log_request(self, code="-", size="-"):
        """Registro compacto de solicitudes en consola."""
        self.log_message("%s %s %s", self.command, self.path.split("?", 1)[0], code)


def main():
    """Punto de entrada de la aplicación web."""
    parser = argparse.ArgumentParser(description="Sistema local de reservas de pasajes")
    parser.add_argument("--puerto", type=int, default=8000)
    parser.add_argument("--db", type=Path, default=Path("reservas.sqlite3"))
    parser.add_argument("--trabajadores", type=int, default=2, choices=range(1, 9))
    opciones = parser.parse_args()
    ruta = opciones.db.resolve()
    
    # Inicialización del inventario y modo WAL
    inicializar(ruta)
    
    # Ejecución del servidor concurrente
    with Servidor(opciones.puerto, ruta, opciones.trabajadores) as servidor:
        print(f"Reservas de pasajes: http://127.0.0.1:{servidor.server_port}", flush=True)
        try:
            servidor.serve_forever()
        except KeyboardInterrupt:
            pass
        finally:
            servidor.procesos.shutdown()


if __name__ == "__main__":
    main()
