/*
1. Se simulan dos ventanillas que reservan asientos de un bus compartido,
   evitando reservas duplicadas mediante un semaforo POSIX entre procesos.
2. Linux o WSL, con C++17:
   g++ -std=c++17 -Wall -Wextra -Wpedantic -pthread reservas_pasajes.cpp -o reservas_pasajes
   ./reservas_pasajes
3. El proceso principal crea dos hijos con fork(): Ventanilla 1 y Ventanilla 2.
4. Este avance incluye 10 asientos, solicitudes fijas, exclusion mutua,
   contadores compartidos, comprobacion final y liberacion de recursos.
5. Para entregas posteriores quedan venta web, cancelaciones, pagos,
   multiples buses, interfaz grafica y base de datos.
*/

#include <cerrno>
#include <cstdio>
#include <cstdlib>
#include <csignal>
#include <semaphore.h>
#include <sys/mman.h>
#include <sys/wait.h>
#include <unistd.h>

constexpr int CANTIDAD_ASIENTOS = 10;
constexpr int CANTIDAD_SOLICITUDES = 5;

struct DatosCompartidos {
    int asientos[CANTIDAD_ASIENTOS]; // 0: libre; 1 o 2: ventanilla propietaria.
    sem_t exclusion;
    int reservas[2];
};

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

int atender(DatosCompartidos* datos, int ventanilla, const int* solicitudes) {
    for (int intento = 0; intento < CANTIDAD_SOLICITUDES; ++intento) {
        const int numero = solicitudes[intento];
        if (numero < 1 || numero > CANTIDAD_ASIENTOS) {
            std::fprintf(stderr, "Ventanilla %d PID %ld: asiento %d fuera de rango\n",
                         ventanilla, static_cast<long>(getpid()), numero);
            return EXIT_FAILURE;
        }
        if (!adquirir(&datos->exclusion)) {
            return EXIT_FAILURE;
        }
        // Seccion critica: consultar, reservar, contar e imprimir sin intercalado.
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
        if (sem_post(&datos->exclusion) == -1) {
            std::perror("sem_post");
            return EXIT_FAILURE;
        }
        if (impresion < 0 || vaciado == EOF) {
            std::fprintf(stderr, "Error al escribir el resultado\n");
            return EXIT_FAILURE;
        }
        // Demora solo visual, fuera de la seccion critica. La sincronizacion
        // la garantiza el semaforo, no esta demora ni el orden de fork().
        usleep(10000);
    }
    return EXIT_SUCCESS;
}

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

// Se usa SIGKILL porque un hijo podria haber fallado con el semaforo tomado.
// Recoger todos los hijos evita liberar memoria mientras alguno aun la usa.
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

int main() {
    void* memoria = mmap(nullptr, sizeof(DatosCompartidos), PROT_READ | PROT_WRITE,
                         MAP_SHARED | MAP_ANONYMOUS, -1, 0);
    if (memoria == MAP_FAILED) {
        std::perror("mmap");
        return EXIT_FAILURE;
    }
    auto* datos = static_cast<DatosCompartidos*>(memoria);
    // mmap anonimo entrega memoria en cero: asientos y contadores inician en 0.
    if (sem_init(&datos->exclusion, 1, 1) == -1) {
        std::perror("sem_init");
        if (munmap(memoria, sizeof(DatosCompartidos)) == -1) std::perror("munmap");
        return EXIT_FAILURE;
    }
    const int solicitudes[2][CANTIDAD_SOLICITUDES] = {{1, 3, 5, 7, 9}, {2, 3, 6, 7, 10}};
    pid_t hijos[2] = {0, 0};
    bool correcto = true;
    bool recogidos = true;
    for (int indice = 0; indice < 2; ++indice) {
        const pid_t hijo = fork();
        if (hijo == -1) {
            std::perror("fork");
            correcto = false;
            recogidos = terminar_y_recoger(hijos);
            break;
        }
        if (hijo == 0) {
            const int resultado = atender(datos, indice + 1, solicitudes[indice]);
            const int vaciado = std::fflush(stdout);
            _exit(vaciado == EOF ? EXIT_FAILURE : resultado);
        }
        hijos[indice] = hijo;
    }
    // Ambos hijos ya fueron creados. Esperar cualquiera permite detectar un
    // fallo aunque el otro este bloqueado en sem_wait().
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
        correcto = correcto && contados[0] == datos->reservas[0]
            && contados[1] == datos->reservas[1] && total == 8
            && datos->asientos[3] == 0 && datos->asientos[7] == 0;
        std::printf("Verificacion: %s\n", correcto ? "CORRECTA" : "ERROR");
    }
    if (!recogidos) {
        // Ante un error inesperado de espera, no destruir un semaforo en uso.
        return EXIT_FAILURE;
    }
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
