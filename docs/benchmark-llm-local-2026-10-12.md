# Medici?n local de correcci?n LLM para Instant2

**Fecha:** 12 de octubre de 2026 | **Versi?n:** Instant2 v0.1.0
**Objetivo:** medir la correcci?n de transcripciones con el servidor local configurado usando solo ejemplos sint?ticos.

## Equipo y servicio

| Recurso | Medici?n observada |
|---|---|
| CPU | AMD Ryzen 5 7600, 6 n?cleos / 12 procesadores l?gicos |
| RAM | 15,1 GiB total; 4,7 GiB libres en la muestra |
| GPU | AMD Radeon RX 6800 XT de 16 GB; llama.cpp us? `Vulkan0` con 99 capas GPU |
| Runtime | `llama-server`, 6 hilos, endpoint local `127.0.0.1:8080` |
| Modelo | `qwen3.5:4b-q4_K_M` (`Qwen_Qwen3.5-4B-Q4_K_M.gguf`) |

Durante la medici?n, una muestra del proceso indic? 1,28 GiB de memoria residente y 5,44 GiB de memoria privada. Windows mostr? unos 3,16 GiB de memoria dedicada de GPU para el proceso. Son muestras puntuales, no picos ni promedios de una sesi?n completa.

## Resultados de correcci?n

La corrida us? 20 frases sint?ticas. No se envi? audio ni texto de usuario.

| M?trica | Resultado |
|---|---:|
| CER de referencia antes/despu?s | 0,055 ? 0,030 |
| Coincidencia exacta antes/despu?s | 0 % ? 45 % |
| Propuestas que cambiaron palabras | 20 % |
| Propuestas rechazadas por la comprobaci?n conservadora | 5 % |
| Propuestas l?xicas aceptadas | 15 % |
| Anclas del conjunto conservadas | 14 de 14 |
| Primera respuesta, modelo ya cargado | 0,391 s |
| Latencia caliente p50 / p95 | 0,408 s / 0,551 s |

La ruta medida aplica primero correcciones locales, obtiene una propuesta de `polish_candidate()` y hace una segunda comprobaci?n conservadora cuando el texto cambia. Las propuestas que no pasan, la respuesta ambigua y el fallo del servidor conservan el texto corregido localmente. La primera solicitud se midi? con el modelo ya cargado: no es el tiempo de arranque en fr?o.

## L?mites

El corpus es sint?tico y peque?o; no mide la precisi?n de Parakeet ni representa voz espont?nea, distintos micr?fonos o computadoras sin GPU. La coincidencia exacta y CER no determinan por s? solos la fidelidad sem?ntica. Conservar 14 anclas solo verifica elementos elegidos para este conjunto, no garantiza que el sistema no pueda cometer errores en otras frases.

La evaluaci?n de modelos y cuantizaciones en una m?quina sirve para filtrar candidatos, no para recomendar uno a todas las personas. Hace falta medir en CPU y GPU de distintos niveles, con transcripciones de referencia revisadas y audios que se usen con consentimiento. Los audios privados de evaluaci?n no deben subirse a Git o CI.
