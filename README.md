# Sistema de reservas de pasajes

Primer avance académico para el curso de **Sistemas Operativos**, desarrollado en C++17. Simula dos ventanillas que intentan reservar asientos del mismo bus y utiliza sincronización entre procesos reales para evitar reservas duplicadas.

## Alcance

- Un bus de 10 asientos.
- Un proceso principal y dos procesos hijos creados con `fork()`.
- Cinco solicitudes predefinidas por ventanilla.
- Memoria compartida con `mmap()` y exclusión mutua mediante un semáforo POSIX.
- Registro de resultados con rol, PID y asiento, y verificación de contadores al finalizar.

La implementación está en un único archivo: [`reservas_pasajes.cpp`](reservas_pasajes.cpp).

## Requisitos

- Linux o Windows con WSL, ejecutando los comandos dentro de Linux.
- `g++` compatible con C++17.
- No se necesitan bibliotecas externas ni base de datos.

## Compilar y ejecutar

Desde la carpeta del repositorio:

```bash
g++ -std=c++17 -Wall -Wextra -Wpedantic -pthread reservas_pasajes.cpp -o reservas_pasajes
./reservas_pasajes
```

La opción `-pthread` habilita el soporte necesario para las primitivas POSIX; el programa utiliza procesos, sin crear hilos. El ejecutable generado se llama `reservas_pasajes`.

## Funcionamiento

El proceso principal reserva e inicializa la memoria compartida antes de llamar a `fork()`. Crea ambos hijos antes de esperar su finalización con `waitpid()`. Después muestra los asientos y contadores, comprueba su coherencia y libera los recursos.

Cada hijo ejecuta únicamente las solicitudes de su ventanilla y termina con `_exit()` después de vaciar la salida.

| Proceso hijo | Asientos solicitados |
| --- | --- |
| Ventanilla 1 | 1, 3, 5, 7, 9 |
| Ventanilla 2 | 2, 3, 6, 7, 10 |

Los asientos se muestran del 1 al 10 y se convierten a índices del 0 al 9, validando previamente el rango.

### Memoria compartida

La región creada con `MAP_SHARED | MAP_ANONYMOUS` contiene un arreglo fijo de 10 asientos, dos contadores de reservas exitosas y un semáforo inicializado mediante `sem_init()` con `pshared = 1` y valor inicial 1.

| Valor de un asiento | Estado |
| --- | --- |
| 0 | Disponible |
| 1 | Reservado por la ventanilla 1 |
| 2 | Reservado por la ventanilla 2 |

`fork()` por sí solo proporciona espacios de memoria separados: modificar una variable normal en un hijo no actualiza la del padre ni la del otro hijo. El mapeo compartido permite que los tres procesos observen las modificaciones de estos datos.

### Sección crítica

Para cada solicitud, el hijo adquiere el semáforo con `sem_wait()`, consulta la disponibilidad y, si corresponde, registra al propietario e incrementa su contador. También imprime el resultado y vacía el búfer dentro de la misma sección crítica. Finalmente libera el semáforo con `sem_post()`.

La consulta y la actualización son indivisibles respecto de la otra ventanilla, por lo que ambas no pueden reservar el mismo asiento. Las demoras breves se realizan fuera de la sección crítica y solo facilitan observar la ejecución; la sincronización depende del semáforo.

### Manejo de errores

Se comprueban las operaciones de memoria, semáforos, creación y espera de procesos. Las llamadas a `sem_wait()` y `waitpid()` se reintentan ante `EINTR`. Si falla la creación de un hijo o un hijo termina con error, el principal termina y recoge los hijos restantes antes de liberar los recursos.

## Resultados esperados y validación

En una ejecución correcta:

- Los hijos tienen PID distintos y realizan cinco intentos cada uno.
- Los asientos 3 y 7 se reservan una sola vez cada uno.
- Se producen ocho reservas exitosas y dos rechazos.
- Los asientos 4 y 8 quedan libres.
- Los contadores coinciden con los ocho asientos ocupados.
- Se imprime `Verificacion: CORRECTA` y el programa termina con código 0.

El orden de los mensajes y la ventanilla que obtiene los asientos 3 y 7 pueden variar según la planificación del sistema operativo.

Para ejecutar con un límite externo de tres segundos y detectar posibles bloqueos:

```bash
timeout 3s ./reservas_pasajes
echo $?
```

El código 0 indica finalización correcta; 124 indica que `timeout` agotó el límite. Durante la validación inicial se comprobaron 40 ejecuciones con este límite, sin bloqueos y con los resultados esperados.

## Próxima entrega

Quedan pendientes la venta web, cancelaciones, pagos, múltiples buses, interfaz gráfica y persistencia en una base de datos. Este avance se centra en demostrar memoria compartida y exclusión mutua entre procesos.
