# Tecnolog?a y LLM local en Instant2

**Fecha de las mediciones de referencia:** 12 de octubre de 2026.
**Alcance:** reconocimiento de voz local, correcci?n de texto, eficiencia y runtime local.

## Resumen

Instant2 usa Parakeet TDT v3 int8 para reconocer voz y puede usar un LLM de texto configurado para revisar la transcripción. El LLM no recibe audio y no reemplaza al ASR. El audio permanece en el equipo; Ajustes identifica si el servicio de lenguaje configurado es local o remoto. El instalador de Windows incluye llama.cpp Vulkan con respaldo a CPU; Qwen 4B Q4_K_M lo descarga la app en el primer uso, como los paquetes portable, Linux y macOS.

En el conjunto sint?tico medido, Qwen3.5 4B Q4_K_M dio el mejor equilibrio entre correcci?n y tiempo de respuesta de las variantes comparadas. Es un resultado de esta computadora, no una recomendaci?n universal. El modelo local puede permanecer en RAM o VRAM a cargo del runtime durante el uso de Instant2; el comportamiento depende de la configuraci?n del servidor y del hardware.

## Medici?n sint?tica del 12 de octubre

La prueba compar? 20 frases de dictado. Se us? llama.cpp con Vulkan sobre Ryzen 5 7600, 6 hilos y Radeon RX 6800 XT de 16 GB. El baseline local obtuvo 0 % de coincidencia exacta y CER 0,055.

| Modelo y cuantizaci?n | Coincidencia exacta | CER | GPU: p50 de correcci?n | CPU: p50 de correcci?n |
|---|---:|---:|---:|---:|
| Qwen3.5 2B Q4_K_M | 15 % | 0,025 | 0,14 s | 0,59 s |
| Qwen3.5 2B Q8_0 | 20 % | 0,025 | 0,16 s | ? |
| Qwen3.5 4B Q4_K_M | 45 % | 0,019 | 0,26 s | 1,35 s |
| Qwen3.5 4B Q8_0 | 50 % | 0,018 | 0,29 s | ? |
| Llama 3.2 3B Instruct Q4_K_M | 0 % | 0,042 | 0,17 s | ? |

La medici?n detallada de la configuraci?n local de Qwen3.5 4B Q4 registr? CER de 0,055 a 0,030, coincidencia exacta de 0 % a 45 %, latencia caliente p50 de 0,408 s y p95 de 0,551 s. La muestra solo incluy? 20 frases y no midi? audio real ni equipos adicionales. Ver [medici?n local de correcci?n LLM](benchmark-llm-local-2026-10-12.md).

## Qu? falta comparar

- Audios y transcripciones de referencia variados, con consentimiento.
- Equipos solo CPU, port?tiles con poca memoria y GPU de gama media.
- Tiempo de arranque en fr?o, memoria pico y consumo en reposo con el modelo residente.
- Correcciones de puntuaci?n, tildes, nombres, cifras, negaciones y restricciones por separado.
- Fallos del servidor y comportamiento del fallback local.

El cambio de WER/CER y el tiempo deben revisarse junto a los errores concretos. Una mejora de puntuaci?n no compensa que el modelo altere un nombre, una cifra o la intenci?n.

## Decisi?n de implementaci?n

1. Conservar Parakeet como reconocedor de voz local. Los errores ac?sticos deben evaluarse en el ASR; el LLM solo puede revisar el texto que recibe.
2. Mantener la ruta de correcci?n mejorada: correcciones locales, `maybe_polish()` con el servicio configurado y comprobaci?n conservadora de cambios. Ante fallo se conserva el texto local.
3. Reutilizar el endpoint compatible con OpenAI configurado en Ajustes. El runtime local administra el servidor que ya instal? la persona y puede mantener el modelo residente.
4. Dejar runtime y modelos fuera del instalador. Detectar recursos ayuda a elegir CPU o acelerador compatible, pero cada servidor decide su reparto real de RAM y VRAM.
5. No recomendar una cuantizaci?n como universal hasta medir latencia, memoria y fidelidad en m?s equipos.

## Fuentes t?cnicas

- [Sherpa-onnx: Parakeet TDT v3](https://k2-fsa.github.io/sherpa/onnx/pretrained_models/offline-transducer/nemo-transducer-models.html)
- [Parakeet TDT v3 int8](https://huggingface.co/csukuangfj/sherpa-onnx-nemo-parakeet-tdt-0.6b-v3-int8/tree/main)
- [Etiquetas Qwen3.5 en Ollama](https://ollama.com/library/qwen3.5/tags)
- [Tarjeta oficial de Qwen3.5 4B](https://huggingface.co/Qwen/Qwen3.5-4B)
- [Servidor HTTP de llama.cpp](https://github.com/ggml-org/llama.cpp/blob/master/tools/server/README.md)

Las cifras provienen de una PC y un corpus sint?tico peque?o. No prueban la precisi?n del reconocimiento de voz, calidad general del espa?ol, rendimiento en otros equipos ni necesidades pico de memoria.
