# Instant2 ???

**Versi?n 0.1.0 ? Aplicaci?n independiente**

**Dictado por voz en espa?ol con reconocimiento local y correcci?n opcional con LLM.** Manten? **F9** mientras habl?s y solt? para insertar el texto. Pod?s personalizar la tecla en Ajustes.

[?? Descargar](https://github.com/getodevel-source/Instant2/releases/latest) ? [?? Gu?a](dictado/README.md) ? [?? Cambios](CHANGELOG.md)

## Qu? hace

| | |
|---|---|
| ?? **Audio local** | El audio se procesa en tu equipo. El LLM recibe texto, nunca el audio. Ajustes indica si el servicio configurado es local o remoto. |
| ? **Reconocimiento eficiente** | Parakeet TDT v3 int8 reconoce la voz en CPU y no requiere una GPU. |
| ?? **Correcci?n mejorada** | Instant2 aplica correcciones locales y puede usar el LLM configurado para corregir errores de reconocimiento, ortograf?a y puntuaci?n. |
| **Paquete sin conexi?n** | El instalador completo de Windows incluye Parakeet, Qwen 4B, Kokoro con Dora y llama.cpp. |

## Empezar

1. Descarg? la versi?n para tu sistema desde [Releases](https://github.com/getodevel-source/Instant2/releases/latest).
2. Abr? Instant2, eleg? el micr?fono y cambi? la tecla de dictado si quer?s.
3. Enfoc? un campo de texto, manten? F9 mientras habl?s y solt? al terminar.
4. El instalador completo de Windows deja el LLM local Qwen listo. En otros paquetes, pod?s configurar un servicio compatible con OpenAI en Ajustes.

El instalador completo de Windows incluye unos 3.74 GiB de modelos y 87 MiB de runtime. Para instalar sin red, descargá el `.exe` y todas las partes `.bin` y mantenelas juntas. El portable y los paquetes de Linux/macOS descargan sus modelos en el primer uso.

| Sistema | Descarga |
|---|---|
| Windows | `Instant2-Setup.exe` (instalador) o `Instant2.exe` (portable) |
| Linux (X11; Wayland/Hyprland experimental) | `instant2-linux` |
| macOS | `instant2-macos` |

> Antes de dictar, acept? el permiso del micr?fono. Los binarios no est?n firmados por ahora: SmartScreen de Windows y Gatekeeper de macOS avisan la primera vez. Linux X11 funciona directamente; Wayland est? en fase experimental y requiere registrar el atajo de Hyprland como explica la [gu?a detallada](dictado/README.md#linux-x11-y-wayland). En macOS autoriz? tambi?n **Accesibilidad** para permitir el pegado.

## C?mo se procesa el dictado

El audio va a Parakeet en el equipo. Despu?s, Instant2 aplica sus correcciones locales y, si la revisi?n LLM est? activada, env?a el texto reconocido al servicio configurado. Una comprobaci?n conservadora revisa cambios que pueden afectar las palabras o el sentido. Si el LLM no responde o la propuesta no pasa la comprobaci?n, se conserva el texto corregido localmente.

El instalador completo de Windows configura Qwen en local. Ajustes indica si el servicio es local o remoto; si eleg?s uno remoto, el texto dictado sale del equipo. Instant2 no guarda audio ni transcripciones en sus registros o historial por defecto y tampoco registra el texto enviado al LLM.

## Aplicaci?n independiente

Instant2 parte del c?digo de Instant, pero tiene su propio repositorio, instalador, comando (`instant2`), actualizaciones, configuraci?n y modelos. No reutiliza ni modifica los datos de una instalaci?n de Instant. Configur? en Instant2 su propia conexi?n OpenAI compatible desde Ajustes; la URL y la clave no se copian entre aplicaciones.

La configuraci?n se guarda en `%APPDATA%\instant2` en Windows y `~/.config/instant2` en Linux. Los modelos se guardan en `%LOCALAPPDATA%\instant2\models` o `~/.local/share/instant2/models`, respectivamente.

## M?s informaci?n

[Instalaci?n y uso detallados](dictado/README.md) ? [Precisi?n y motores](docs/precision.md) ? [Historial de versiones](CHANGELOG.md) ? [Licencia MIT](LICENSE) ? [Arquitectura del LLM local](docs/arquitectura-runtime-llm-local.md)

## Lectura de texto en voz alta

La pantalla **Leer texto** convierte texto escrito o pegado en voz en español con Kokoro, ejecutándose localmente por CPU. El instalador completo de Windows ya incluye el modelo y la voz Dora; en el portable y en otros sistemas la primera lectura descarga unos 326 MB. Instant2 incluye solo la voz Dora y permite ajustar la velocidad. El texto solo vive en memoria durante la lectura: no se envía a un servicio ni se guarda. El modelo usa [Apache-2.0](https://huggingface.co/hexgrad/Kokoro-82M) y la implementación ONNX usa [MIT](https://github.com/thewh1teagle/kokoro-onnx/blob/main/LICENSE).
