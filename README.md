# Pi Sentinel

Vigilancia local de una habitación con una Raspberry Pi: detecta personas, reconoce a las personas
autorizadas con reconocimiento facial **que se ejecuta íntegramente en el dispositivo**, y cuando
confirma una presencia **desconocida o no verificada** hace sonar una alarma local y envía una
notificación push mediante [ntfy](https://docs.ntfy.sh/).

> **Estado honesto del proyecto.** Todo el código y las pruebas automatizadas se desarrollaron y
> ejecutaron en un equipo x86_64 **sin cámara, sin audio y sin Raspberry Pi**. El rendimiento en la Pi,
> la cámara real, el audio real y la entrega real de ntfy al móvil **no están validados** (ver
> [Estado de validación](#estado-de-validación)).

La aplicación habla siempre de personas «desconocidas» o «no verificables», nunca de intrusos
confirmados: el sistema puede equivocarse.

## Qué hace

- Cámara con captura continua, cola de un solo fotograma (siempre el más reciente), recuperación
  ante errores y liberación del dispositivo al cerrar.
- Detección de personas local (YOLOX, ONNX vía OpenCV DNN) con umbrales y resolución configurables,
  y «puerta de movimiento» para no analizar escenas quietas.
- Reconocimiento facial local (YuNet + SFace) con perfiles autorizados, calidad de muestra,
  umbral configurable y tres estados: `AUTHORIZED`, `UNKNOWN`, `UNVERIFIED`.
- Seguimiento de presencias: un evento por presencia, confirmación por tiempo y observaciones,
  salida por ausencia prolongada, nueva entrada = nuevo evento.
- **Sonido de entrada**: un timbre breve en la Pi cuando se confirma que alguien ha entrado (autorizada, desconocida o no verificable, a elegir), con sonido incluido de serie y elegible o subible desde tu dispositivo.
- Alarma sonora local (sin Internet): sonido propio, volumen, duración, repeticiones, intervalo
  mínimo, silencio temporal.
- Notificaciones ntfy con cola persistente en SQLite, reintentos con espera progresiva, estados
  `PENDING`/`SENT`/`FAILED`, límites de tamaño y antigüedad, y sin duplicados.
- Interfaz web local en español (Panel, Cámara, Personas, Alarmas, Notificaciones, Historial, Ajustes).
- Modo prueba: analiza y muestra lo que *haría*, sin alarma, sin ntfy y sin historial.

## Hardware y compatibilidad

| Elemento | Requisito |
|---|---|
| Placa | Raspberry Pi 4 (≥ 2 GB) o Pi 5 recomendadas. Pi 3 / Zero 2 W: posible con ajustes mínimos, no probado. |
| SO | Raspberry Pi OS **64 bits** (Bookworm). También funciona en Debian/Ubuntu x86_64/aarch64. |
| Python | 3.9 o superior |
| Cámara | USB (V4L2) o módulo oficial de Raspberry Pi (Picamera2, **sin validar en hardware**) |
| Audio | Salida de audio con `aplay`/`paplay`/`pw-play` (paquete `alsa-utils`) |
| Red | No necesaria para detectar, reconocer ni sonar. Solo para ntfy. |

Elecciones tecnológicas (y por qué):

| Función | Elección | Licencia | Motivo |
|---|---|---|---|
| Personas | YOLOX (opencv_zoo) | Apache-2.0 | Se descartó YOLOv8/Ultralytics por su licencia AGPL |
| Caras | YuNet 2023mar | MIT | Muy ligero, soportado por `cv2.FaceDetectorYN` |
| Reconocimiento | SFace 2021dec | Apache-2.0 | Embedding de 128-d, soportado por `cv2.FaceRecognizerSF`; evita instalar ONNX Runtime/PyTorch |
| Persistencia | SQLite | — | Sin servidor, robusto |
| Web | FastAPI + JS sin dependencias | — | Sin CDN: funciona sin Internet |

Los modelos se descargan de `opencv/opencv_zoo` y se **verifican por SHA-256** (`scripts/download_models.py`).
No se probó InsightFace (modelos con licencia de uso no comercial y mayor tamaño).

## Instalación

```bash
git clone <este repositorio> pi-sentinel && cd pi-sentinel
./install.sh            # interactivo; añade --yes --service para no preguntar e iniciar con el sistema
```

El instalador: comprueba arquitectura/SO/modelo de Pi/RAM, instala dependencias de apt (te lo explica
antes y usa `sudo` solo para eso), crea un entorno virtual (`--system-site-packages` en Raspberry Pi para
`picamera2`), instala dependencias de Python, descarga y verifica los modelos, crea `.env` y `data/`
con permisos restrictivos, ejecuta `python -m app.main check` (cámara, audio, modelos) y, si quieres,
instala el servicio `systemd`. `./install.sh --dry-run` muestra lo que haría sin tocar nada.

Ejecución manual: `.venv/bin/python -m app.main` y abre <http://127.0.0.1:8765>.

Comandos útiles:

```bash
python -m app.main check          # diagnóstico de compatibilidad
python -m app.main benchmark      # latencias de los modelos en ESTE equipo (ejecútalo en la Pi)
python -m app.main set-password   # contraseña de administración (obligatoria para abrir a la red)
```

## Primeros pasos

1. **Registrar tu rostro.** En la primera ejecución se abre *Personas*: crea tu perfil y captura
   muestras siguiendo la guía (de frente, ligeramente a cada lado, con otra luz, otra expresión).
   Se rechazan muestras con cara pequeña, borrosa, girada, cortada, oscura o con varias personas.
   Se necesitan **5 como mínimo**; **8-10 con variedad** funcionan mejor. Más muestras *no garantizan*
   una identificación perfecta.
2. **Probar el reconocimiento** («Probar ahora» / «Prueba continua»): muestra el estado y la similitud
   de cada cara frente a cada perfil.
3. **Modo prueba** (Panel): comprueba la imagen y lo que ocurriría sin sonar ni enviar nada.
4. **Activar vigilancia.** No se permite sin al menos un perfil activo con muestras suficientes
   (si no, tú mismo serías «desconocido»). Si los perfiles desaparecen durante la vigilancia, pasa
   a modo prueba.

Coloca la cámara de modo que **vea la zona de acceso (puerta) y las caras de frente y a distancia
razonable**. Una cámara no puede saber que alguien cruzó una puerta que no ve.

## Calibrar el reconocimiento

La «similitud» es un coseno entre representaciones faciales: **no es una probabilidad**. El umbral
por defecto (0,363) es la referencia publicada para SFace, no un valor calibrado para tu habitación.
Guía detallada en [docs/CALIBRACION.md](docs/CALIBRACION.md). Resumen:

- *Falsas alarmas contigo* (te trata como desconocido): baja el umbral un poco, añade muestras con
  la luz real de la habitación, sube la resolución o acerca la cámara.
- *Desconocidos aceptados* (falso positivo de identidad): sube el umbral y revisa que no haya
  muestras de otra persona en tu perfil.
- *Calibración asistida* (Personas → Umbral): mide tu rostro y, si puedes, el de alguien no
  registrado; si las puntuaciones se separan sugiere un punto medio. Si se solapan, ningún umbral
  basta: mejora luz/ángulo/muestras.
- Ningún ajuste elimina por completo los errores.

## Configurar ntfy

1. En la interfaz → **Notificaciones** → «Generar tópico aleatorio» (≈192 bits de entropía).
2. En el móvil instala la app **ntfy** (Android/iOS), «Suscribirse a un tema» e introduce el tópico
   (botón «Mostrar»; por defecto está oculto para no salir en capturas).
3. Activa «Enviar notificaciones push» y guarda. Pulsa **«Enviar notificación de prueba»**: es la
   única forma de enviar un aviso de prueba (nunca se envían solos).

**Servidor público vs. privado.** En `https://ntfy.sh` el tópico actúa como contraseña débil: quien lo
conozca puede leer tus avisos. Un tópico aleatorio es difícil de adivinar, pero **no es autenticación**.
Para algo más sólido, usa un servidor ntfy propio con control de acceso y un **token** (campo «Token»;
se guarda en `data/secrets.json` con permisos 0600 o en `PI_SENTINEL_NTFY_TOKEN`, y nunca se muestra
completo). Las notificaciones no contienen fotos, nombres ni datos biométricos: solo fecha, hora,
dispositivo, identificador de evento y estado.

La entrega inmediata, el sonido y la vibración dependen del sistema operativo del móvil, de los
permisos, del ahorro de batería y de la conectividad. Una notificación **no equivale a una llamada**
ni se garantiza al instante.

Sin Internet: la detección, el reconocimiento y la alarma siguen funcionando; las alertas quedan en
cola (`PENDING`) y se reintentan con espera exponencial (5 s → máx. 15 min). Se descartan —y se
registra— las que superan la antigüedad máxima (24 h por defecto) o rebasan la cola (200). Si el
equipo se apaga justo durante un envío, ese aviso podría repetirse al reiniciar (entrega
«al menos una vez»).

## Alarmas

*Alarmas*: sonido (WAV PCM 16 bit propio o la sirena integrada), volumen, duración máxima, repeticiones,
intervalo mínimo entre alarmas, activar/desactivar sin detener la vigilancia, «Probar», «Detener»,
«Silenciar 30 min». Si no hay audio, se muestra el error claro y la vigilancia y ntfy continúan.

**Sonido de entrada** (Alarmas → Sonido de entrada): suena al confirmarse una presencia, **aparte** de la
alarma y de ntfy. Viene activado con el timbre `sounds/entrada.wav` (CC0, ver `sounds/LICENCIAS.md`). Puedes
cambiarlo por otro de los incluidos o subir un `.wav` desde tu dispositivo (Alarmas → Sonido propio) y
elegirlo en la lista; elige para qué estados suena y su volumen, duración, repeticiones e intervalo. Reglas:
no suena en modo prueba; si ya suena (o acaba de sonar) la alarma de desconocido no se superpone; funciona
aunque la política de «autorizada» sea ignorar (esa política solo afecta al historial); «Silenciar» y
«Detener» cubren ambos sonidos. Los sonidos muy bajos se normalizan al reproducir (máx. ×8).

Políticas por estado (Alarmas → Confirmación y política): `AUTHORIZED`: ignorar; `UNKNOWN`: alertar;
`UNVERIFIED`: solo registrar (modo estricto: alertar). Además: tiempo de confirmación, observaciones
mínimas, ausencia para «salida», tiempo entre alertas.

### Altavoz Bluetooth o «no suena nada»

Pi Sentinel reproduce con `pw-play` (PipeWire) → `paplay` (PulseAudio) → `aplay` (ALSA), en ese orden, y
usa **la salida predeterminada del sistema**. Para un altavoz Bluetooth:

1. Empareja y conecta el altavoz en el sistema (`bluetoothctl`, o el icono de Bluetooth del escritorio) y
   márcalo como salida predeterminada (`wpctl status` / `pactl get-default-sink`). Si Pi Sentinel suena en
   HDMI/jack en vez de en el altavoz, el predeterminado no es el altavoz.
2. Ejecuta `python -m app.main audio-test`: muestra reproductores y salidas y reproduce un tono con cada
   reproductor para que oigas cuál funciona. Fija el que suene en *Alarmas → Reproductor de audio* (y, si
   hace falta, la salida concreta, p. ej. `bluez_output.XX_XX_XX_XX_XX_XX.1`).
3. Si lo ejecutas como **servicio systemd**, necesita llegar a tu sesión de audio: el instalador añade
   `XDG_RUNTIME_DIR` y propone `sudo loginctl enable-linger <usuario>` para que esa sesión (y la conexión
   Bluetooth) exista sin tener el escritorio abierto. Si instalaste antes de este cambio, vuelve a ejecutar
   `./install.sh --service` o añade esa variable a la unidad.
4. El silencio previo (0,5 s por defecto) evita que el altavoz «dormido» se coma el inicio; súbelo si aún
   se corta. «Probar» ahora espera el resultado real y muestra el error del reproductor si falla.

Limitación: si el reproductor termina sin error pero el sonido sale por otra salida, no hay forma de
detectarlo desde la aplicación; `audio-test` y la salida predeterminada son la guía.

## Iniciar y detener la vigilancia, inicio automático, registros

- Panel → *Activar vigilancia* / *Modo prueba* / *Detener*. El estado se recuerda al reiniciar.
- Inicio automático: `./install.sh --service` o `sudo systemctl enable --now pi-sentinel`.
  Desactivar: `sudo systemctl disable --now pi-sentinel`. El servicio reinicia solo ante fallos
  (máx. 5 en 5 min), con `MemoryMax=1G`.
- Registros: `journalctl -u pi-sentinel -f` y `data/logs/sentinel.log` (rotativo 5×1 MB). Los tokens,
  cabeceras `Authorization` y el tópico se redactan.

## Acceso desde otros dispositivos

Por defecto la interfaz escucha **solo en `127.0.0.1`**. Para abrirla a tu red doméstica:
`python -m app.main set-password`, luego `PI_SENTINEL_ALLOW_LAN=1` y `PI_SENTINEL_HOST=0.0.0.0`
(o Ajustes → Acceso desde la red) y reinicia. Sin contraseña, el arranque se **rechaza**. Hay sesión con
cookie `HttpOnly`/`SameSite=Strict`, token CSRF, validación de `Host`, límite de peticiones y de
tamaño, y bloqueo progresivo de intentos de acceso. **No la expongas a Internet.** HTTP plano en la
LAN no cifra la contraseña: usa un proxy inverso con TLS o `PI_SENTINEL_TLS_CERT/KEY`. Tras un
proxy, todos los clientes parecen la misma IP para los límites.

## Datos que guarda y cómo borrarlos

| Dato | Dónde | Notas |
|---|---|---|
| Embeddings faciales (128 números por muestra) | `data/sentinel.db` | **Datos biométricos**: permisos 0600 en carpeta 0700 |
| Nombres de perfiles, configuración | `data/sentinel.db` | |
| Eventos (fecha, id, estado, alarma, notificación) | `data/sentinel.db` | Retención configurable (30 días) |
| Cola/historial de notificaciones (título y texto) | `data/sentinel.db` | Sin biometría |
| Token de ntfy, hash de la contraseña (scrypt) | `data/secrets.json` | 0600 |
| Sonidos subidos por ti | `data/sounds/` | Solo audio |
| Registros | `data/logs/` | Sin secretos |

**No se guarda** vídeo, fotos, imágenes de desconocidos ni capturas; los fotogramas se procesan en
memoria. Nada biométrico sale del equipo. Borrado: perfil individual o muestra (Personas), «Eliminar
todo» (Personas → Datos biométricos, con `VACUUM`), «Borrar historial» (Historial) o `./scripts/uninstall.sh`.
Los datos biométricos requieren protección especial y, según tu jurisdicción y a quién grabes
(visitas, inquilinos, empleados), pueden requerir consentimiento o aviso. Eso es responsabilidad tuya.

## Limitaciones conocidas

- El reconocimiento facial puede fallar en ambos sentidos: poca luz, contraluz, gafas, mascarilla,
  ángulos extremos, personas parecidas.
- **Sin detección de vida**: una foto, un vídeo o una pantalla frente a la cámara podrían confundirlo.
- Rostros pequeños, lejanos o de espaldas → `UNVERIFIED`, no `UNKNOWN`.
- Si una persona autorizada y otra ocupan el mismo sitio sin hueco, el seguimiento podría fusionarlas.
- Salida/entrada: una presencia termina tras `exit_seconds` sin verla (10 s). Un reingreso dentro del
  intervalo de «tiempo entre alertas» (30 s) se registra pero no vuelve a alertar.
- La cámara no ve lo que no está en su campo; no sabe si alguien cruzó una puerta fuera de plano.
- En una Pi 3/Zero el análisis puede ir a <1 imagen/s; ajusta resolución del detector (320) y frecuencia.

## Estado de validación

Pruebas automatizadas: `pip install -r requirements-dev.txt && python -m pytest` (191 pruebas, datos
sintéticos). El sonido de entrada se probó con audio simulado y su carga/validación con el fichero real; **no se ha oído por un altavoz real** y el timbre se eligió por duración y formato, no por escucha. **Ejecutadas aquí (x86_64, sin hardware):** todas pasan. Además se verificaron, en este equipo:
arranque real de la aplicación, parada ordenada con SIGTERM, permisos de ficheros, rechazo de enlace
a red sin contraseña, la interfaz completa en Chromium (flujo de registro, modo prueba, vigilancia, historial,
alarma, ntfy, login), y los tres modelos reales con imágenes públicas de muestra.

Rendimiento medido **en x86_64 de 4 núcleos (no en Raspberry Pi)**: YOLOX 320 px ≈ 37 ms, 416 px ≈ 58 ms,
640 px ≈ 145 ms; YuNet 640×480 ≈ 9 ms. **No extrapoles a la Pi**: ejecuta `python -m app.main benchmark`.

### Pruebas de aceptación

| # | Condición | Estado |
|---|---|---|
| 1 | La aplicación se inicia | ✅ comprobado (x86) |
| 2 | Cámara se abre y cierra de forma segura | ✅ lógica con cámara simulada · ⏳ cámara real pendiente |
| 3 | Registro del rostro del propietario | ✅ con modelos reales y simulados · ⏳ con cámara real pendiente |
| 4 | Añadir y eliminar perfiles | ✅ |
| 5 | Detector identifica personas en imágenes de prueba | ✅ (imagen pública de muestra, manual) y lógica con salida simulada |
| 6 | Distingue autorizado/desconocido/no verificable | ✅ lógica · ⏳ precisión real sin medir |
| 7 | Presencia desconocida confirmada genera evento | ✅ |
| 8 | El evento activa la alarma | ✅ con audio simulado · ⏳ altavoz real pendiente |
| 9 | El evento solicita ntfy | ✅ contra servidor HTTP local |
| 10 | El móvil recibe la notificación | ⏳ **no verificado** (el entorno no alcanza ntfy.sh) |
| 11 | Sin notificaciones repetidas en una presencia | ✅ |
| 12 | Fallos de Internet no detienen la vigilancia | ✅ |
| 13 | Pendientes según política | ✅ |
| 14 | Credenciales no aparecen en registros | ✅ |
| 15 | Parar y reiniciar sin bloquear la cámara | ✅ cámara simulada (se liberan todas las aperturas) · ⏳ real pendiente |

## Solución de problemas

| Síntoma | Causa / solución |
|---|---|
| «No se pudo abrir la cámara» | Usuario sin grupo `video`; dispositivo en uso; elige otro en Cámara. Módulo Pi: instala `python3-picamera2` y usa el controlador Picamera2. |
| «No se encontró ningún reproductor de audio» | `sudo apt install alsa-utils`; prueba `aplay -l`; fija el dispositivo ALSA en Alarmas. |
| «Faltan modelos» | `python scripts/download_models.py` |
| Tú saltas como desconocido | Mira *Calibrar*; añade muestras con la luz real; baja el umbral. |
| Mucha CPU / lento | Detector a 320, 1 análisis/s, captura a 5 fps, activa «analizar con movimiento». |
| ntfy: «Autenticación rechazada» | Token incorrecto o tópico protegido; edítalo y «Reintentar fallidas». |
| `Host no permitido` | Accediste con un nombre no listado: usa `PI_SENTINEL_ALLOWED_HOSTS=mi-pi.local`. |

## Actualizar y desinstalar

`./scripts/update.sh` (copia de `data/`, `git pull --ff-only`, dependencias, modelos, reinicio).
`./scripts/uninstall.sh` (quita el servicio y el venv; pregunta antes de borrar los datos).

## Estructura

```
app/            camera · person_detector · face_recognition · authorized_people · presence_tracker
                event_manager · sound_alert · ntfy_client · notification_queue · database · security
                config · service (orquestador) · main · web/ (API + interfaz)
scripts/        download_models.py · update.sh · uninstall.sh · pi-sentinel.service.in
tests/          pytest (191) · ui_harness.py (servidor de desarrollo con cámara simulada)
docs/           ARQUITECTURA.md · CALIBRACION.md
```

Más detalle de diseño: [docs/ARQUITECTURA.md](docs/ARQUITECTURA.md).
