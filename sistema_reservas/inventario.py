"""
Módulo de inventario persistente y concurrencia de datos.
Implementa el modelo de Lectores-Escritores y exclusión mutua mediante SQLite.
"""

from contextlib import closing
import secrets
import sqlite3

# Canales de venta concurrentes soportados por el sistema
CANALES = ("Web", "Ventanilla 1", "Ventanilla 2")


class OperacionInvalida(ValueError):
    """Excepción lanzada cuando una regla de negocio o restricción de concurrencia falla."""
    pass


def conectar(ruta):
    """
    Establece conexión con la base de datos SQLite con timeout defensivo.
    - timeout=5: Espera hasta 5 segundos si otro proceso escritor retiene el candado de escritura.
    - row_factory: Permite acceder a las columnas por nombre (tipo diccionario).
    - foreign_keys = ON: Activa la integridad referencial del motor relacional.
    """
    conexion = sqlite3.connect(ruta, timeout=5)
    conexion.row_factory = sqlite3.Row
    conexion.execute("PRAGMA foreign_keys = ON")
    return conexion


def inicializar(ruta):
    """
    Crea el esquema inicial de base de datos y configura el modo de sincronización.
    
    FUNDAMENTO DE SISTEMAS OPERATIVOS:
    - 'PRAGMA journal_mode = WAL' activa el Write-Ahead Logging.
      En modo WAL, las lecturas no bloquean a las escrituras y las escrituras no bloquean
      a las lecturas (Problema clásico de Lectores-Escritores resuelto por instantáneas).
    - El índice parcial 'asiento_activo' garantiza la exclusión mutua: sólo puede existir
      una reserva activa (cancelada IS NULL) para cada tupla (bus_id, asiento).
    """
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
            -- Exclusión mutua granular: una sola reserva activa por asiento
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
    """
    Operación de sólo lectura: obtiene el estado actual de los buses y asientos ocupados.
    
    FUNDAMENTO DE SISTEMAS OPERATIVOS:
    - Abre una transacción con 'BEGIN' en modo lectura compartida.
    - Gracias a WAL, lee una instantánea coherente del inventario sin impedir
      que los procesos trabajadores sigan procesando reservas simultáneamente.
    """
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
    """
    Consulta puntual de una reserva por su token alfanumérico.
    Lectura atómica de solo lectura que une los datos del boleto con la información del bus.
    """
    with closing(conectar(ruta)) as conexion:
        fila = conexion.execute(
            """SELECT reservas.*, buses.ruta FROM reservas
               JOIN buses ON buses.id = reservas.bus_id WHERE codigo = ?""",
            (codigo,),
        ).fetchone()
        return dict(fila) if fila else None


def reservar(conexion, bus_id, asiento, canal):
    """
    Sección crítica de reserva de asiento dentro de una transacción exclusiva ya iniciada.
    
    PREVENCIÓN DE CONDICIONES DE CARRERA:
    - Valida existencia del bus y rango válido del asiento.
    - Utiliza 'INSERT INTO ... ON CONFLICT(...) DO NOTHING' respaldado por el índice parcial.
    - Si otro proceso ganó la carrera para el mismo asiento en ese instante, el motor no inserta nada.
    - Al evaluar rowcount != 1, detectamos el conflicto y lanzamos OperacionInvalida (HTTP 409).
    """
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
    """
    Despachador central de operaciones de escritura ejecutadas por los procesos trabajadores.
    
    PREVENCIÓN DE DEADLOCK Y SERIALIZACIÓN:
    - Inicia con 'BEGIN IMMEDIATE'. Esto adquiere inmediatamente un bloqueo de escritura
      reservado sobre la base de datos, impidiendo que dos procesos comiencen como lectores
      y luego intenten escalar a escritores simultáneamente (la causa principal de deadlock en SQLite).
    - Garantiza atomicidad completa (ACID) para reservar, pagar y cancelar.
    - Máquina de estados:
        * reservar: crea la reserva en estado 'Pendiente de pago'.
        * pagar: pasa a 'Pagada' (idempotente si ya estaba pagada; rechaza si fue cancelada).
        * cancelar: marca cancelada con timestamp (libera el asiento inmediatamente gracias
          a la condición 'WHERE cancelada IS NULL' del índice único, y preserva la trazabilidad).
    """
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
