#pragma once

#include <semaphore.h>
#include <sys/types.h>

// ============================================================================
// Constantes del sistema de reservas de bajo nivel
// ============================================================================
constexpr int CANTIDAD_ASIENTOS = 10;
constexpr int CANTIDAD_SOLICITUDES = 5;

// ============================================================================
// Estructura en Memoria Compartida (mmap anónimo)
// ============================================================================
// Representa el recurso compartido entre los procesos creados con fork().
// Al ser asignado mediante mmap() anónimo, inicia con todos los bytes en cero.
struct DatosCompartidos {
    int asientos[CANTIDAD_ASIENTOS]; // 0: libre; 1 o 2: ventanilla propietaria.
    sem_t exclusion;                 // Semáforo POSIX para proteger la sección crítica.
    int reservas[2];                 // Contadores de reservas exitosas por ventanilla.
};

// ============================================================================
// Prototipos de funciones de sincronización y control de procesos
// ============================================================================

// Adquiere el semáforo POSIX manejando interrupciones por señales (EINTR)
bool adquirir(sem_t* semaforo);

// Ejecuta las solicitudes de reserva de una ventanilla dentro de la sección crítica
int atender(DatosCompartidos* datos, int ventanilla, const int* solicitudes);

// Espera la finalización de un proceso hijo manejando EINTR
pid_t esperar(pid_t hijo, int* estado);

// Envía SIGKILL y recoge los procesos hijos en caso de error o finalización
bool terminar_y_recoger(pid_t hijos[2]);
