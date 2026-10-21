# Lista de validación de una release instalada

Esta lista acompaña a los smokes de CI. CI verifica la suite, el empaquetado y
la apertura del panel congelado; esta hoja registra los pasos que dependen de
instalar la aplicación en un escritorio real.

## Registro de ejecución

- Tag probado:
- Fecha y persona que probó:
- Equipo, versión del sistema y tipo de sesión:
- Versión instalada previamente (si corresponde):
- Artefacto y SHA256 descargados:
- Resultado general: pendiente / aprobado / fallido
- Incidencias y enlaces:

## Matriz de plataformas

| Plataforma | Artefacto | Cobertura automatizada actual | Validación de escritorio pendiente |
|---|---|---|---|
| Windows | `Instant2-Setup.exe` y `Instant2.exe` | Suite Windows; smoke del portable congelado. El instalador se construye en CI y no lleva los pesos: la app baja el reconocimiento sola en el primer uso y el modelo de lenguaje local (3 GB) a pedido desde Ajustes. | Instalación limpia, actualización, rollback, firma si está activa y desinstalación. |
| macOS | `instant2-macos`; `Instant2-macos.dmg` si la firma está activa | Suite macOS; smoke del binario congelado. | Permiso de micrófono, Accesibilidad, Gatekeeper/notarización si está activa, actualización y desinstalación. |
| Linux X11 | `instant2-linux` | Suite Ubuntu; smoke del panel congelado en runner. | Instalación limpia, dependencias, X11, autostart, actualización y desinstalación. |
| Arch/Omarchy Wayland | `instant2-linux` | Pruebas unitarias de selección de backend, dependencias y pegado. | Portal de atajos de Hyprland, aplicaciones nativas Wayland, XWayland y pegado real. |

## Instalación limpia

- [ ] Descargar el artefacto de la plataforma y comprobar su SHA256 contra el
  sidecar publicado.
- [ ] Instalar o ejecutar como usuario normal, sin elevar permisos salvo que el
  instalador del sistema lo solicite para instalar la aplicación.
- [ ] Abrir Instant2 y completar los permisos de micrófono/accesibilidad que
  correspondan.
- [ ] Confirmar que el diagnóstico detecta dependencias faltantes y propone
  comandos adecuados para el sistema.
- [ ] Con un directorio de datos vacío, abrir el panel y comprobar que la
  descarga del reconocimiento arranca sola, con progreso, y que se puede
  cancelar; tras cancelar, comprobar que el botón «Reintentar descarga» sigue
  funcionando y que la app no vuelve a insistir sola.
- [ ] Iniciar una descarga del modelo; verificar espacio libre/requerido,
  progreso, cancelación y reanudación tras cancelar o cortar la red.
- [ ] En Ajustes → Revisión del dictado y privacidad → Archivos del servicio
  local, confirmar que el modelo de lenguaje (3 GB) aparece como «no
  descargado» y que **no** se baja solo; descargarlo a pedido, viendo el
  progreso y comprobando que se puede cancelar.
- [ ] Completar un dictado en una aplicación de texto y confirmar el resultado.
- [ ] Cerrar y volver a abrir: comprobar que configuración, modelos y
  preferencia de inicio se conservaron.

## Modelos de la versión anterior

- [ ] Con modelos en `%LOCALAPPDATA%\instant\models` (el directorio del
  producto anterior) y sin `%LOCALAPPDATA%\instant2\models`, abrir la app y
  comprobar que los modelos se reutilizan, que no se vuelven a descargar y que
  el directorio anterior queda vacío o desaparece.
- [ ] Repetir el caso con los dos directorios existentes: lo que falta se
  mueve y la copia duplicada del directorio anterior desaparece.

## Actualización y recuperación

Preparar una instalación de la release anterior y guardar una copia de su
configuración y lista de modelos antes de probar.

- [ ] Actualizar desde la release anterior a la nueva y confirmar que la
  configuración y los modelos quedan intactos.
- [ ] Confirmar que un hash incorrecto o sidecar inválido impide aplicar la
  descarga.
- [ ] Simular un reemplazo fallido en una copia desechable de la instalación;
  comprobar que el ejecutable anterior sigue disponible y puede iniciarse.
- [ ] Confirmar que no se interrumpe una sesión de dictado sin aviso.
- [ ] Desinstalar y confirmar que configuración/modelos se conservan por
  defecto; probar la opción de borrado explícito solo en datos desechables.

## Casos específicos

### Actualización desde la aplicación (Windows portable)

Esta comprobación verifica el actualizador integrado contra una release
publicada. Descargar o reemplazar el ejecutable desde el navegador, el terminal
o el explorador de archivos no satisface este paso.

- [ ] Confirmar que Instant2 muestra la versión anterior al pie del panel y
  que no hay un dictado en curso.
- [ ] Abrir **Ajustes → Actualizaciones**, elegir **Avisarme** y pulsar
  **Buscar actualizaciones**.
- [ ] Confirmar que se identifica la nueva versión; comenzar la descarga desde
  el botón de la aplicación y esperar a que confirme el SHA-256.
- [ ] Aceptar la aplicación de la actualización dentro de Instant2. Dejar que
  el actualizador detenga los procesos de esa copia portable, reemplace el
  ejecutable y vuelva a abrir la aplicación.
- [ ] Confirmar en la aplicación abierta que muestra la nueva versión, que el
  daemon inicia correctamente y que su configuración se conserva.
- [ ] Anotar versión anterior/nueva, fecha, modo portable y el resultado de
  cada paso; la salida de CI o un hash obtenido fuera de la aplicación no
  demuestra el ciclo de actualización integrado.

### Windows

- [ ] Probar instalador y portable por separado.
- [ ] Verificar que abrir el panel y dictar no requiere permisos de
  administrador.
- [ ] Si la release se firmó, revisar el publicador en Propiedades → Firmas
  digitales y comprobar el artefacto con `signtool verify /pa /v`.

### macOS

- [ ] Autorizar micrófono y Accesibilidad.
- [ ] Si se publica el DMG notarizado, abrirlo con Gatekeeper y confirmar la
  evaluación local con `spctl --assess --type open --verbose Instant2.app`.
- [ ] Probar dictado en una aplicación nativa y pegar caracteres con acentos.

### Linux X11 y Arch/Omarchy Wayland

- [ ] Probar el hotkey de mantener y soltar, el portapapeles previo y el pegado.
- [ ] En Hyprland, confirmar que el portal queda registrado y asociar
  `instant2-push-to-talk` siguiendo la [guía Linux](../dictado/README.md#hyprland-y-omarchy).
- [ ] Probar una aplicación Wayland nativa y otra XWayland por separado; anotar
  las diferencias.
- [ ] Probar paquetes/dependencias con `apt` en Debian/Ubuntu y `pacman` en
  Arch/Omarchy.

No se debe marcar esta lista como ejecutada basándose solo en mocks, suite,
render fuera de pantalla o build. Completar una copia por sistema y adjuntar el
log de diagnóstico sin incluir audio, transcripciones, tokens ni datos
personales.
