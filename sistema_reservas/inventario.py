"""Inventario persistente de la simulación."""

from contextlib import closing
import secrets
import sqlite3

CANALES = ("Web", "Ventanilla 1", "Ventanilla 2")


class OperacionInvalida(ValueError):
    pass


def conectar(ruta):
    conexion = sqlite3.connect(ruta, timeout=5)
    conexion.row_factory = sqlite3.Row
    conexion.execute("PRAGMA foreign_keys = ON")
    return conexion


def inicializar(ruta):
    with closing(conectar(ruta)) as conexion, conexion:
        conexion.execute("PRAGMA journal_mode = WAL")
        conexion.executescript("""
            CREATE TABLE IF NOT EXISTS buses (
                id INTEGER PRIMARY KEY,
                ruta TEXT NOT NULL,
                asientos INTEGER NOT NULL CHECK (asientos > 0),
                tarifa_centimos INTEGER NOT NULL CHECK (tarifa_centimos > 0)
            );
            CREATE TABLE IF NOT EXISTS reservas (
                codigo TEXT PRIMARY KEY,
                bus_id INTEGER NOT NULL REFERENCES buses(id),
                asiento INTEGER NOT NULL CHECK (asiento > 0),
                canal TEXT NOT NULL CHECK (canal IN ('Web', 'Ventanilla 1', 'Ventanilla 2')),
                importe_centimos INTEGER NOT NULL CHECK (importe_centimos > 0),
                creada TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
                pagada TEXT,
                cancelada TEXT
            );
            CREATE UNIQUE INDEX IF NOT EXISTS asiento_activo
            ON reservas(bus_id, asiento) WHERE cancelada IS NULL;
        """)
        conexion.executemany(
            "INSERT OR IGNORE INTO buses VALUES (?, ?, ?, ?)",
            [(1, "Arequipa → Cusco", 10, 6500),
             (2, "Arequipa → Puno", 12, 4500),
             (3, "Arequipa → Tacna", 16, 5000)],
        )


def consultar_buses(ruta):
    with closing(conectar(ruta)) as conexion:
        conexion.execute("BEGIN")
        buses = [dict(bus) for bus in conexion.execute("SELECT * FROM buses ORDER BY id")]
        ocupados = {bus["id"]: set() for bus in buses}
        for fila in conexion.execute(
            "SELECT bus_id, asiento FROM reservas WHERE cancelada IS NULL"
        ):
            ocupados[fila["bus_id"]].add(fila["asiento"])
        for bus in buses:
            bus["ocupados"] = ocupados[bus["id"]]
        return buses


def consultar_reserva(ruta, codigo):
    with closing(conectar(ruta)) as conexion:
        fila = conexion.execute(
            """SELECT reservas.*, buses.ruta FROM reservas
               JOIN buses ON buses.id = reservas.bus_id WHERE codigo = ?""",
            (codigo,),
        ).fetchone()
        return dict(fila) if fila else None


def reservar(conexion, bus_id, asiento, canal):
    bus = conexion.execute("SELECT * FROM buses WHERE id = ?", (bus_id,)).fetchone()
    if bus is None or not 1 <= asiento <= bus["asientos"]:
        raise OperacionInvalida("El bus o el asiento seleccionado no existe.")
    if canal not in CANALES:
        raise OperacionInvalida("El canal de venta no es válido.")

    codigo = secrets.token_urlsafe(16)
    resultado = conexion.execute(
        """INSERT INTO reservas (codigo, bus_id, asiento, canal, importe_centimos)
           VALUES (?, ?, ?, ?, ?)
           ON CONFLICT(bus_id, asiento) WHERE cancelada IS NULL DO NOTHING""",
        (codigo, bus_id, asiento, canal, bus["tarifa_centimos"]),
    )
    if resultado.rowcount != 1:
        raise OperacionInvalida("Ese asiento ya está reservado. Selecciona otro.")
    return codigo


def aplicar_operacion(ruta, operacion, *argumentos):
    with closing(conectar(ruta)) as conexion, conexion:
        conexion.execute("BEGIN IMMEDIATE")
        if operacion == "reservar":
            return reservar(conexion, *argumentos)
        codigo, = argumentos
        fila = conexion.execute(
            "SELECT pagada, cancelada FROM reservas WHERE codigo = ?", (codigo,)
        ).fetchone()
        if fila is None:
            raise OperacionInvalida("No encontramos una reserva con ese código.")
        if operacion == "pagar":
            if fila["cancelada"]:
                raise OperacionInvalida("La reserva ya fue cancelada.")
            if not fila["pagada"]:
                conexion.execute(
                    "UPDATE reservas SET pagada = CURRENT_TIMESTAMP WHERE codigo = ?", (codigo,)
                )
        elif operacion == "cancelar":
            if not fila["cancelada"]:
                conexion.execute(
                    "UPDATE reservas SET cancelada = CURRENT_TIMESTAMP WHERE codigo = ?", (codigo,)
                )
        else:
            raise ValueError("Operación desconocida")
        return codigo
