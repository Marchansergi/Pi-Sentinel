# Guía de calibración

Objetivo: reducir **falsas alarmas** (te toma por desconocido) y **falsos reconocimientos** (acepta a un
desconocido como autorizado). Ambos errores se compensan: no pueden eliminarse del todo.

## 1. Mejora primero la entrada, luego el umbral

1. **Cámara:** a la altura de la cara, enfocando la zona de entrada, sin contraluz (no frente a una ventana).
2. **Luz:** la misma que habrá cuando vigiles (de día y de noche). Las muestras de día no preparan la noche.
3. **Resolución/distancia:** una cara por debajo de ~56 px de ancho se descarta («no verificable»). Si
   tu habitación es grande, sube resolución de captura (p. ej. 1280×720) o acerca la cámara.
4. **Muestras:** 8-10 por persona con variedad (frontal, ligeramente girado a cada lado, gafas si las usas,
   luz distinta). Elimina las dudosas. No mezcles personas en un perfil (la app lo detecta si la cara no
   se parece a las existentes).

## 2. Mide con «Probar el reconocimiento»

Con la vigilancia en modo prueba, colócate en los sitios y a las horas habituales. Anota la **similitud**
que obtienes contra tu perfil (típicamente claramente por encima del umbral cuando funciona bien).

## 3. Calibración asistida

*Personas → Umbral y calibración*:

1. **Medir mi rostro** (≈12 imágenes válidas): da mín., p10, media y máx. de tus similitudes.
2. **Medir persona no registrada** (alguien que viva/visite y NO esté registrado): da su similitud máxima
   contra tu perfil.
3. Si `máx. del no registrado < p10 propio`, se sugiere el punto medio. Pruébalo y repite en otra luz.
4. Si se solapan, ningún umbral es bueno en esas condiciones: vuelve al paso 1.

Referencia: 0,363 es el umbral recomendado por OpenCV para SFace (LFW), **no** el óptimo de tu habitación.

## 4. Tabla de síntomas

| Síntoma | Qué probar |
|---|---|
| Te marca *Desconocida* con frecuencia | Más muestras con esa luz/ángulo; bajar el umbral 0,02-0,05; aumentar *observaciones mínimas* o *tiempo de confirmación* (más tolerancia a fallos aislados) |
| Marca *No verificable* a menudo | Cara pequeña/borrosa/girada: acercar cámara, mejorar luz, ajustar *tamaño mínimo* o *nitidez mínima* con cuidado |
| Acepta a un desconocido como autorizado | Subir el umbral; revisar perfil contaminado; añadir más muestras tuyas (mejora la separación) |
| Alertas demasiado tardías | Bajar *tiempo de confirmación* y *observaciones mínimas* (más falsas alarmas) |
| Mucha CPU | Detector 320, 1 análisis/s, solo con movimiento |

## 5. Sobre la política de «no verificable»

Por defecto solo se registra. El **modo estricto** (alertar) cubre a quien se tapa la cara o pasa de
espaldas, pero también generará avisos cuando tú estés de espaldas o lejos. Úsalo solo si la cámara ve
la puerta de frente.

Esta guía no promete eliminar errores: revisa el historial y reajusta periódicamente.
