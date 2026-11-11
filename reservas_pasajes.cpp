/*
================================================================================
SISTEMA DE RESERVAS DE PASAJES — DEMOSTRACIÓN DE BAJO NIVEL (POSIX / LINUX)
Curso: Sistemas Operativos
================================================================================
Conceptos de Sistemas Operativos demostrados en este archivo:
1. Llamadas al sistema para creación y jerarquía de procesos: fork().
2. Asignación de memoria compartida anónima entre procesos: mmap().
3. Sincronización y exclusión mutua mediante semáforos POSIX: sem_t.
4. Sección crítica indivisible: verificación, asignación e incremento de reservas.
5. Manejo de señales (SIGKILL) y recolección de procesos hijos zombis: waitpid().
6. Tratamiento robusto de interrupciones del kernel: manejo de errno == EINTR.
================================================================================
*/

#include "reservas_pasajes.hpp"

#include <cerrno>
#include <cstdio>
#include <cstdlib>
#include <csignal>
#include <sys/mman.h>
#include <sys/wait.h>
#include <unistd.h>

/**
 * Adquiere un semáforo POSIX de forma segura.
 * Reintenta automáticamente si una señal del sistema operativo interrumpe
 * la llamada al sistema sem_wait() antes de adquirir el bloqueo (EINTR).
 */
bool adquirir(sem_t* semaforo) {
    int resultado;
    do {
        resultado = sem_wait(semaforo);
    } while (resultado == -1 && errno == EINTR);
    if (resultado == -1) {
        std::perror("sem_wait");
        return false;
    }
    return true;
}

/**
 * Lógica ejecutada por cada proceso hijo (Ventanilla 1 y Ventanilla 2).
 * Procesa una lista de solicitudes de asientos protegiendo la sección crítica.
 */
int atender(DatosCompartidos* datos, int ventanilla, const int* solicitudes) {
    for (int intento = 0; intento < CANTIDAD_SOLICITUDES; ++intento) {
        const int numero = solicitudes[intento];
        if (numero < 1 || numero > CANTIDAD_ASIENTOS) {
            std::fprintf(stderr, "Ventanilla %d PID %ld: asiento %d fuera de rango\n",
                         ventanilla, static_cast<long>(getpid()), numero);
            return EXIT_FAILURE;
        }

        // ====================================================================
        // ENTRADA A LA SECCIÓN CRÍTICA
        // ====================================================================
        if (!adquirir(&datos->exclusion)) {
            return EXIT_FAILURE;
        }

        // SECCIÓN CRÍTICA:
        // Consulta del estado, asignación del asiento, conteo de reservas
        // e impresión en consola protegidas para evitar entrelazado de salida.
        const int indice = numero - 1;
        const bool disponible = datos->asientos[indice] == 0;
        if (disponible) {
            datos->asientos[indice] = ventanilla;
            ++datos->reservas[ventanilla - 1];
        }
        const int impresion = std::printf("Ventanilla %d PID %ld: asiento %d -> %s\n",
            ventanilla, static_cast<long>(getpid()), numero,
            disponible ? "RESERVADO" : "RECHAZADO");
        const int vaciado = std::fflush(stdout);

        // ====================================================================
        // SALIDA DE LA SECCIÓN CRÍTICA
        // ====================================================================
        if (sem_post(&datos->exclusion) == -1) {
            std::perror("sem_post");
            return EXIT_FAILURE;
        }

        if (impresion < 0 || vaciado == EOF) {
            std::fprintf(stderr, "Error al escribir el resultado\n");
            return EXIT_FAILURE;
        }

        // Demora pedagógica solo visual, fuera de la sección crítica.
        // La exclusión mutua la garantiza el semáforo POSIX, no esta pausa.
        usleep(10000);
    }
    return EXIT_SUCCESS;
}

/**
 * Espera a que un proceso hijo cambie de estado, reintentando ante EINTR.
 */
pid_t esperar(pid_t hijo, int* estado) {
    pid_t resultado;
    do {
        resultado = waitpid(hijo, estado, 0);
    } while (resultado == -1 && errno == EINTR);
    if (resultado == -1) {
        std::perror("waitpid");
    }
    return resultado;
}

/**
 * Gestión defensiva de ciclo de vida de procesos:
 * Si un hijo falla o queda bloqueado, envía SIGKILL para prevenir procesos colgados
 * y recoge todos los procesos hijos pendientes para evitar procesos zombis antes
 * de destruir el semáforo o liberar la memoria compartida.
 */
bool terminar_y_recoger(pid_t hijos[2]) {
    bool recogidos = true;
    for (int indice = 0; indice < 2; ++indice) {
        if (hijos[indice] > 0 && kill(hijos[indice], SIGKILL) == -1 && errno != ESRCH) {
            std::perror("kill");
        }
    }
    for (int indice = 0; indice < 2; ++indice) {
        if (hijos[indice] > 0) {
            int estado = 0;
            if (esperar(hijos[indice], &estado) == -1 && errno != ECHILD) {
                recogidos = false;
            }
            hijos[indice] = 0;
        }
    }
    return recogidos;
}

/**
 * Función principal: Inicialización de recursos del SO, bifurcación y verificación.
 */
int main() {
    // 1. Asignación de memoria compartida anónima a nivel de kernel (mmap)
    void* memoria = mmap(nullptr, sizeof(DatosCompartidos), PROT_READ | PROT_WRITE,
                         MAP_SHARED | MAP_ANONYMOUS, -1, 0);
    if (memoria == MAP_FAILED) {
        std::perror("mmap");
        return EXIT_FAILURE;
    }
    auto* datos = static_cast<DatosCompartidos*>(memoria);

    // 2. Inicialización del semáforo POSIX:
    // pshared = 1 indica que el semáforo es compartido entre procesos (no solo hilos).
    // valor inicial = 1 (semáforo binario para exclusión mutua).
    if (sem_init(&datos->exclusion, 1, 1) == -1) {
        std::perror("sem_init");
        if (munmap(memoria, sizeof(DatosCompartidos)) == -1) std::perror("munmap");
        return EXIT_FAILURE;
    }

    // Solicitudes fijas donde los asientos 3 y 7 entran en disputa directa
    const int solicitudes[2][CANTIDAD_SOLICITUDES] = {{1, 3, 5, 7, 9}, {2, 3, 6, 7, 10}};
    pid_t hijos[2] = {0, 0};
    bool correcto = true;
    bool recogidos = true;

    // 3. Creación de procesos concurrentes con fork()
    for (int indice = 0; indice < 2; ++indice) {
        const pid_t hijo = fork();
        if (hijo == -1) {
            std::perror("fork");
            correcto = false;
            recogidos = terminar_y_recoger(hijos);
            break;
        }
        if (hijo == 0) {
            // Código ejecutado exclusivamente por el proceso hijo
            const int resultado = atender(datos, indice + 1, solicitudes[indice]);
            const int vaciado = std::fflush(stdout);
            _exit(vaciado == EOF ? EXIT_FAILURE : resultado);
        }
        hijos[indice] = hijo;
    }

    // 4. Proceso padre: monitoreo y espera de terminación de los hijos
    if (correcto) {
        for (int pendientes = 2; pendientes > 0; --pendientes) {
            int estado = 0;
            const pid_t terminado = esperar(-1, &estado);
            if (terminado > 0) {
                for (int indice = 0; indice < 2; ++indice) {
                    if (hijos[indice] == terminado) hijos[indice] = 0;
                }
            }
            if (terminado == -1 || !WIFEXITED(estado) || WEXITSTATUS(estado) != 0) {
                std::fprintf(stderr, "Un hijo fallo; se detienen las ventanillas restantes\n");
                correcto = false;
                recogidos = terminar_y_recoger(hijos);
                break;
            }
        }
    }

    // 5. Verificación de integridad de los datos compartidos
    if (correcto) {
        int contados[2] = {0, 0};
        std::printf("\nPrincipal PID %ld: estado final\n", static_cast<long>(getpid()));
        for (int indice = 0; indice < CANTIDAD_ASIENTOS; ++indice) {
            const int propietario = datos->asientos[indice];
            if (propietario == 0) {
                std::printf("Asiento %d: LIBRE\n", indice + 1);
            } else if (propietario == 1 || propietario == 2) {
                ++contados[propietario - 1];
                std::printf("Asiento %d: Ventanilla %d\n", indice + 1, propietario);
            } else {
                correcto = false;
            }
        }
        const int total = datos->reservas[0] + datos->reservas[1];
        std::printf("Reservas exitosas: Ventanilla 1 = %d; Ventanilla 2 = %d; total = %d\n",
                    datos->reservas[0], datos->reservas[1], total);
        
        // Validación estricta: 8 reservas, 2 asientos libres (4 y 8), coherencia de contadores
        correcto = correcto && contados[0] == datos->reservas[0]
            && contados[1] == datos->reservas[1] && total == 8
            && datos->asientos[3] == 0 && datos->asientos[7] == 0;
        std::printf("Verificacion: %s\n", correcto ? "CORRECTA" : "ERROR");
    }

    if (!recogidos) {
        return EXIT_FAILURE;
    }

    // 6. Liberación ordenada de recursos del Sistema Operativo
    if (sem_destroy(&datos->exclusion) == -1) {
        std::perror("sem_destroy");
        correcto = false;
    }
    if (munmap(memoria, sizeof(DatosCompartidos)) == -1) {
        std::perror("munmap");
        correcto = false;
    }
    return correcto ? EXIT_SUCCESS : EXIT_FAILURE;
}
