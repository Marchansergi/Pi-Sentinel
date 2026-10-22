# Arquitectura y decisiones

## Flujo

```
Cámara (hilo) ──último fotograma──► Bucle de análisis (hilo único)
                                       │ puerta de movimiento
                                       ├─ PersonDetector (YOLOX)   ─► personas
                                       ├─ FaceEngine (YuNet+SFace) ─► caras (AUTHORIZED/UNKNOWN/UNVERIFIED)
                                       ▼
                         build_observations (cara ↔ persona)
                                       ▼
                         PresenceTracker  ─ eventos CONFIRMED / RECLASSIFIED / ENDED
                                       ▼
                         EventManager ─ política + tiempo entre alertas
                          ├─ SQLite (events)
                          ├─ SoundAlert (hilo de reproducción)
                          └─ NotificationQueue (SQLite) ─► hilo ntfy ─► HTTP
```

Interfaz web y análisis no comparten estructuras mutables: la configuración se entrega como copias
inmutables (`ConfigStore.get()`), el último fotograma es un objeto inmutable, y SQLite va tras un `RLock`.
Las redes `cv2.dnn` se protegen con candado (no son seguras entre hilos).

## Decisiones principales

- **Un hilo de análisis, cola de tamaño 1.** Si analizar es más lento que capturar, se descarta lo viejo.
  Un error del detector **no** se interpreta como «sala vacía»: el ciclo se omite y el estado del
  componente pasa a error. Sin imagen no se inventan desconocidos.
- **Tres estados, no dos.** `UNVERIFIED` evita acusar de «desconocida» a una persona de espaldas, lejana
  o borrosa. Su política por defecto es solo registrar.
- **Confirmación con ventana deslizante** (10 veredictos): un acierto/fallo aislado no cambia el
  estado. Retirar una alerta de desconocido (→ autorizado) exige más evidencia que confirmarla.
- **Cooldown global de alertas.** Todo evento queda en el historial; solo alerta si pasó el tiempo
  entre alertas. Se prioriza no duplicar avisos frente a avisar de cada reentrada rápida.
- **ntfy por JSON a la raíz del servidor** (`POST {server}/` con `topic/title/message/priority/tags`),
  para enviar UTF-8 (emoji, «—») sin codificar cabeceras. *Esta forma se implementó según la
  documentación de ntfy conocida; no pudo contrastarse en vivo desde el entorno de desarrollo
  (sin acceso a docs.ntfy.sh ni ntfy.sh).* No se usan identificadores de secuencia/idempotencia del
  servidor: la deduplicación es local (`event_id` único). La entrega es «al menos una vez».
- **Cola con circuito:** tras un fallo transitorio se retrasa también el resto de pendientes para no
  martillear un servidor caído. 401/403/400/404 no se reintentan (`FAILED`, con «Reintentar fallidas»).
- **OpenCV DNN para todo.** Menos dependencias en ARM64 (sin ONNX Runtime/PyTorch). Coste: sin GPU/NPU.
  YOLOX acepta entrada dinámica (320/416/640), lo que permite adaptar el coste a la placa.
- **Biometría mínima:** solo embeddings; recortes y fotogramas nunca se escriben. `VACUUM` tras el
  borrado total.
- **Interfaz sin dependencias externas ni estilos en línea** (CSP `default-src 'self'`); texto dinámico
  siempre con `textContent`.
- **`systemd`:** reinicio solo `on-failure`, con límite, `MemoryMax`, `NoNewPrivileges`, `UMask=0077`.
  Uvicorn re-lanza SIGTERM al terminar (código 143), por eso `SuccessExitStatus=143`; el cierre
  ordenado se ejecuta en el *lifespan* de la app.

## Qué no está resuelto

- Detección de vida (anti-foto/pantalla).
- Asociación robusta entre personas que se cruzan (el tracker usa IoU/contención, sin re-identificación).
- Validación en Raspberry Pi real (rendimiento, Picamera2, audio, consumo de memoria sostenido).
- Calibración con datos reales de la habitación (los umbrales por defecto son genéricos).
- Cifrado en reposo de la base de datos (se confía en permisos de fichero; usa cifrado de disco si lo necesitas).
