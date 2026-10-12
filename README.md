# Instant 🎙️

**Dictado por voz en español, 100 % local y privado.** Mantené F9, hablá y soltá: Instant escribe en el campo activo. Sin cuentas, sin nube, sin GPU.

[⬇️ Descargar](https://github.com/getodevel-source/Instant/releases/latest) · [📖 Guía](dictado/README.md) · [📝 Cambios](CHANGELOG.md)

<p align="center">
  <a href="docs/screenshots/instant-home.png"><img src="docs/screenshots/instant-home.png" alt="Inicio de Instant en su ventana compacta" width="100%"></a>
</p>

## Por qué Instant

| | |
|---|---|
| 🔒 **Privado de verdad** | El audio nunca sale de tu equipo. Modelo local, sin telemetría. |
| ⚡ **Rápido** | VoxCore en CPU: tu voz a texto en una fracción de segundo. |
| 🎯 **Preciso en español** | Diccionario técnico, corrección fonética y restauración de `¿?` incluidos. |
| 🪶 **Liviano** | Sin GPU, ~670 MB de modelo que se descarga una sola vez. |

## Empezar

0. **Primera vez:** Instant descarga el modelo de voz (~670 MB, una sola
   vez). Necesitás internet y ~1 GB libre en disco; sin el modelo, el
   dictado no arranca.
1. Descargá la versión para tu sistema desde [Releases](https://github.com/getodevel-source/Instant/releases/latest).
2. Abrí Instant, elegí el micrófono y configurá la tecla.
3. Enfocá cualquier campo de texto, mantené la tecla mientras hablás y soltala al terminar. Listo.

| Sistema | Descarga |
|---|---|
| Windows | `Instant-Setup.exe` (instalador) o `Instant.exe` (portable) |
| Linux (X11; Wayland/Hyprland experimental) | `instant-linux` |
| macOS | `instant-macos` |

> **Antes de dictar.** Tu sistema te pide permiso para usar el micrófono:
> aceptalo, si no Instant no escucha. Los binarios están **sin firmar**
> por ahora: SmartScreen (Windows) y Gatekeeper (macOS) avisan la primera
> vez, es esperable. Linux X11 funciona directamente; Wayland está en fase
> experimental y requiere registrar el atajo de Hyprland como explica la
> [guía detallada](dictado/README.md#linux-x11-y-wayland). En macOS autorizá además
> **Accesibilidad** en Ajustes del Sistema (pegar con el teclado lo exige).

Los releases actuales siguen sin firma de sistema operativo; el workflow ya
permite habilitar firma Windows y notarización macOS al configurar certificados.
Ver [cómo se configura](docs/firma-releases.md).

## Pantallas

<table>
  <tr>
    <th>Micr&oacute;fono</th>
    <th>Ajustes</th>
  </tr>
  <tr>
    <td><a href="docs/screenshots/instant-microphone.png"><img src="docs/screenshots/instant-microphone.png" alt="Selecci&oacute;n y prueba del micr&oacute;fono" width="100%"></a></td>
    <td><a href="docs/screenshots/instant-settings.png"><img src="docs/screenshots/instant-settings.png" alt="Preferencias de Instant" width="100%"></a></td>
  </tr>
  <tr>
    <th>Vocabulario</th>
    <th>Indicador de voz</th>
  </tr>
  <tr>
    <td><a href="docs/screenshots/instant-vocabulary.png"><img src="docs/screenshots/instant-vocabulary.png" alt="Edici&oacute;n del vocabulario personalizado" width="100%"></a></td>
    <td valign="middle" align="center"><a href="docs/screenshots/voice-overlay.png"><img src="docs/screenshots/voice-overlay.png" alt="Indicador de voz durante el dictado" width="100%"></a></td>
  </tr>
</table>

## Privacidad y actualizaciones

El audio no se envía a la nube. Si activás el pulido opcional, el servidor que elijas recibe el texto reconocido y el vocabulario activo, nunca el audio. Instant verifica cada actualización y espera tu confirmación antes de instalarla.

## Plan de Instant2

Consulta el [plan de producto y los flujos gráficos](docs/plan-instant2-flujos.md) para la prioridad de entrega confiable, el modo Prompt y la integración opcional de un LLM local. Es una propuesta de diseño; esas funciones todavía no están implementadas.

## Más información

[Instalación y uso detallados](dictado/README.md) · [Precisión y motores](docs/precision.md) · [Historial de versiones](CHANGELOG.md) · [Licencia MIT](LICENSE)
