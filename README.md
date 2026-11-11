# Sistema de reservas de pasajes

Proyecto académico de **Sistemas Operativos**. La aplicación permite reservar asientos desde una interfaz web local que representa tres canales de venta: Web, Ventanilla 1 y Ventanilla 2. Incluye consulta por código, pago simulado, cancelación y persistencia en SQLite.

## Ejecutar la aplicación

Con Python 3.10 o posterior, desde la raíz del repositorio:

```bash
python3 -m sistema_reservas.web
```

Abre [http://127.0.0.1:8000](http://127.0.0.1:8000). La base `reservas.sqlite3` se crea al iniciar y conserva los datos entre ejecuciones. Los buses, destinos y tarifas son datos de ejemplo. Para cambiar la base, el puerto o la cantidad de trabajadores:

```bash
python3 -m sistema_reservas.web --db otra.sqlite3 --puerto 8080 --trabajadores 3
```

El servidor escucha en la interfaz local. La aplicación usa la biblioteca estándar de Python y los pagos y reembolsos son simulados.

### Probar el flujo

1. Elige un bus, un asiento libre y un canal. Pulsa **Reservar** y guarda el código.
2. Pulsa **Simular pago** y comprueba el estado **Pagada**.
3. Vuelve al inicio y consulta la reserva con su código.
4. Pulsa **Cancelar reserva**. El asiento vuelve a estar disponible. Si tenía un pago, la consulta muestra **Cancelada con reembolso simulado**.

## Sincronización

| Concepto | Aplicación |
| --- | --- |
| Productor–consumidor | El servidor web recibe solicitudes de los tres canales. Un grupo de procesos trabajadores ejecuta las operaciones de escritura. Un semáforo de admisión limita el total de operaciones pendientes y en ejecución a la cantidad de trabajadores más 16. Una solicitud que excede ese límite recibe HTTP 503. |
| Lectores–escritores | Las consultas leen el inventario mediante una instantánea de SQLite en modo WAL. Las escrituras empiezan con `BEGIN IMMEDIATE`, de modo que SQLite serializa a los escritores mientras los lectores pueden consultar el estado confirmado. |
| Exclusión sobre un asiento | Un índice único parcial de SQLite admite una sola reserva activa por combinación de bus y asiento. Cancelar conserva el historial y libera el asiento dentro de la misma transacción. |

Los trabajadores son procesos del sistema operativo creados mediante `multiprocessing`. Cada operación abre su propia conexión. El código de reserva permite consultar y gestionar la reserva desde cualquiera de los canales simulados.

## Demostración original en C++17

`reservas_pasajes.cpp` muestra el caso reducido con un bus y dos hijos creados con `fork()`. Usa memoria compartida con `mmap()` y un semáforo POSIX para proteger la consulta, actualización y conteo de asientos. Ejecútalo en Linux o WSL:

```bash
g++ -std=c++17 -Wall -Wextra -Wpedantic -pthread reservas_pasajes.cpp -o reservas_pasajes
./reservas_pasajes
```

El resultado esperado es de ocho reservas, dos rechazos y la línea `Verificacion: CORRECTA`. Los asientos 3 y 7 se disputan entre las dos ventanillas. Los asientos 4 y 8 quedan libres.

## Prueba automatizada

```bash
python3 -m unittest discover -s tests
```

La prueba envía solicitudes HTTP concurrentes para el mismo asiento, comprueba que solo una se confirme, ejecuta el ciclo de pago y cancelación y reinicia el servidor para verificar la persistencia.
