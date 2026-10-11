# Informe de mejoras posibles para Instant

**Corte de revisión:** 11 de octubre de 2026 · **Versión del repositorio:** `v0.5.1`

Instant ya tiene una base sólida de dictado local, controles de privacidad, recuperación del actualizador y cobertura automatizada en Windows, Linux y macOS. Las mejoras con mayor valor ahora consisten en comprobar los recorridos completos en instalaciones reales, ampliar la evidencia de precisión con distintas voces y equipos, y mantener alineados el producto y sus documentos.

## Resumen ejecutivo

| Prioridad | Mejora | Beneficio principal | Esfuerzo estimado |
|---|---|---|---|
| P1 | Validar instalación, actualización y recuperación en cada sistema | Evitar sorpresas al instalar una release o actualizar una versión existente | Medio |
| P1 | Ampliar la evaluación del dictado con voz real diversa | Detectar errores que los corpus limpios o sintéticos no muestran | Medio |
| P1 | Poner al día el plan de auditoría y registrar evidencia por versión | Dar una hoja de ruta coherente y verificable | Bajo |
| P1 | Dar soporte real a Omarchy/Arch + Hyprland/Wayland | Habilitar el flujo principal de dictado en un escritorio Linux moderno | Alto |
| P2 | Revisar accesibilidad y escalado de la interfaz | Facilitar el uso con teclado, lectores de pantalla y distintas pantallas | Medio |
| P2 | Mejorar la resiliencia comprobada de la primera descarga | Hacer más predecible la configuración inicial con red o disco limitados | Medio |
| P2 | Firmar y notarizar los binarios de escritorio | Facilitar la verificación del publicador y la instalación | Alto |

**Siguiente orden de verificación:** probar el ciclo de dictado en Omarchy; completar la matriz de releases instalada y el corpus consentido; cerrar la revisión manual de lectores de pantalla y activar la firma solo cuando estén listos los certificados.

**Prioridades:** P1 corresponde al siguiente ciclo por impacto en compatibilidad, confianza y calidad; P2 agrupa mejoras posteriores de experiencia. El esfuerzo es relativo e incluye implementación y validación.

## Estado de implementación

| Mejora | Estado en el repositorio | Evidencia o límite restante |
|---|---|---|
| Validación de releases | Lista de aceptación y matriz por sistema creadas. | Falta ejecutar contra instalaciones y artefactos reales; ver [validación de release](validacion-release.md). |
| Voz real diversa | Harness local con consentimiento, WER, S/D/I, vacíos y medición del corrector; datos privados ignorados por Git. | No se generó un baseline porque no hay un corpus nuevo aportado y consentido. |
| Auditoría vigente | Auditoría anterior marcada explícitamente como histórica y enlazada con este informe. | Este archivo queda como referencia de pendientes activos. |
| Omarchy/Arch/Hyprland | Integración Wayland experimental en código: portal GlobalShortcuts, `wl-clipboard`, `wtype`, `pacman`, diagnóstico e instrucciones; bundle Linux incluye el backend. | Hace falta confirmar el ciclo de dictado y pegado en una sesión real de Omarchy, con apps Wayland y XWayland. |
| Accesibilidad y escala | Foco y contraste reforzados, controles de 44 px, escala persistente 100/125/150 %, atajos y ventana mínima de 760 × 480. | El lector de pantalla y recorte visual requieren una revisión manual por sistema; ver [accesibilidad](accesibilidad.md). |
| Descarga inicial | Preflight de espacio visible, cancelación y reanudación de `.part`, retry y errores accionables; hay pruebas automatizadas. | El caso de transferencia real en red lenta/disco pequeño debe verificarse en la matriz de release. |
| Firma y notarización | Workflow opt-in de firma Windows y notarización macOS integrado; sidecars se generan después de firmar. | Queda inactivo hasta configurar la variable y credenciales de firma del repositorio; no se verificó un release firmado aquí. |

**Lectura correcta:** la implementación de código y sus pruebas locales están cerradas donde se indica; las comprobaciones de hardware, credenciales privadas o escritorios ajenos a este entorno siguen pendientes y no se presentan como aprobadas.

## Avance para Omarchy

Se agregó una primera ruta experimental para Arch/Hyprland: Instant solicita el hotkey mediante el portal XDG, recibe eventos de tecla presionada/liberada, pega usando `wl-clipboard` y `wtype`, y el instalador/diagnóstico reconocen `pacman`. La guía explica cómo asociar el atajo desde la configuración de Hyprland.

Falta probarlo en una sesión real de Omarchy, confirmar el backend de portal instalado por defecto y completar una vuelta de dictado en aplicaciones Wayland y XWayland. Hasta esa verificación, el soporte queda marcado como experimental.

## Qué ya está bien encaminado

- **Privacidad:** audio y reconocimiento funcionan localmente. El pulido remoto es opcional y la interfaz explica qué datos se envían si se activa.
- **Actualizaciones:** hay selección de modo (`notify`, `auto`, `off`), verificación SHA256 antes de aplicar y rollback en POSIX cuando falla la sustitución del ejecutable.
- **Entrega:** los workflows corren la suite en Windows, Ubuntu y macOS; la publicación también construye binarios y ejecuta smokes de la interfaz congelada.
- **Medición:** existen benchmarks para WER, corrección de vocabulario y VAD, además de pruebas adversarias para evitar correcciones falsas.

Estas capacidades aparecen en el [changelog de `v0.5.0`](../CHANGELOG.md), el [workflow de pruebas](../.github/workflows/tests.yml), el [workflow de releases](../.github/workflows/release.yml) y el documento de [precisión](precision.md).

## Oportunidades recomendadas

### P1 · Validación instalada de los recorridos de release

**Hallazgo:** la CI verifica la suite por sistema y abre el panel de binarios congelados. La evidencia documentada no cubre aún, de punta a punta, la instalación de una release, su actualización desde una versión anterior y la recuperación cuando el reemplazo falla.

**Propuesta:** crear una lista de aceptación por sistema y ejecutarla contra artefactos de release. Cubrir instalación limpia, primer inicio, descarga o detección del modelo, actualización desde la release anterior, conservación de configuración y modelos, rechazo de un hash inválido, rollback ante fallo y desinstalación.

**Implementado:** la lista de aceptación y la matriz para Windows, macOS, Linux X11 y Omarchy/Wayland están en [validación de release](validacion-release.md). **Pendiente de evidencia:** registrar una ejecución real por sistema.

**Evidencia:** los workflows prueban build y apertura de la interfaz, y la documentación actual señala como pendiente la experiencia instalada real. Véanse [releases](../.github/workflows/release.yml), [guía de instalación](../dictado/README.md) y [changelog](../CHANGELOG.md).

### P1 · Medición de calidad con voces y condiciones variadas

**Hallazgo:** los benchmarks existentes son valiosos para comparar cambios. FLEURS usa 60 clips de lectura limpia y el benchmark de bias parte de frases TTS degradadas. El documento también registra sesiones reales y buenos resultados en un conjunto técnico reducido; queda ampliar la diversidad de hablantes, micrófonos y entornos.

**Propuesta:** mantener un corpus de evaluación local y consentido con habla espontánea en español, ruido moderado, micrófonos distintos, nombres propios, vocabulario técnico y frases generales. Medir WER, omisiones, fragmentos vacíos y falsos positivos del corrector por separado. Conservar audio solo con consentimiento explícito y fuera de cualquier telemetría.

**Implementado:** el harness local [bench_real_audio.py](../dictado/bench/bench_real_audio.py) valida consentimiento y produce métricas agregadas sin exponer referencias ni IDs. El manifiesto y los audios quedan ignorados por Git. **Pendiente de evidencia:** reunir clips con permiso y generar el primer baseline; no se inventó uno. Usar el umbral ya propuesto en el [plan de fine-tuning](plan-finetune.md) para evitar una degradación superior a `+0.005` WER en FLEURS-60.

**Evidencia:** [precisión del dictado](precision.md) documenta FLEURS-60, sesiones reales y los límites de cada medición; los harnesses están en `dictado/bench/`.

### P1 · Sincronizar la auditoría con el estado actual

**Hallazgo:** [`auditoria-plan-mejora-integral.md`](auditoria-plan-mejora-integral.md) conserva referencias a `v0.3.2` y describe el actualizador como pendiente de iniciar la descarga. El [changelog de `v0.5.0`](../CHANGELOG.md) documenta ya modos de actualización, revalidación SHA256, rollback y cambios de privacidad. Esto puede llevar a priorizar tareas cerradas o a interpretar como actual un estado histórico.

**Propuesta:** marcar explícitamente esa auditoría como histórica o actualizar su tabla de estado con fecha, versión y evidencia. Mantener una sola fuente para pendientes activos y enlazar este informe desde ella.

**Implementado:** el documento anterior ahora avisa que es histórico y enlaza el estado vigente y sus listas de validación. Los cierres y pendientes quedan resumidos en la tabla de estado de este informe.

### P2 · Accesibilidad y escalado de la interfaz

**Hallazgo:** el proyecto verifica navegación y render de sus vistas, aunque en los documentos revisados no aparece una validación sistemática de accesibilidad ni de tamaños de pantalla/DPI.

**Propuesta:** revisar los flujos principales con teclado solamente; orden y visibilidad del foco; nombres accesibles de controles; contraste; escalado de texto y contenido a 100 %, 125 %, 150 % y en el tamaño mínimo de ventana. Corregir controles que no puedan operarse o cuyo estado no pueda percibirse sin color.

**Implementado:** controles de foco, contraste, movimiento reducido, escala y áreas interactivas ampliadas; el tamaño mínimo de ventana también se redujo. Los pasos para probar con teclado, NVDA, VoiceOver y Orca están en [accesibilidad](accesibilidad.md). **Pendiente de evidencia:** completar esos recorridos manuales.

### P2 · Primera ejecución y descarga del modelo

**Hallazgo:** la primera ejecución requiere Internet, aproximadamente 670 MB para el modelo y cerca de 1 GB libre. El código ya incluye progreso, verificación y descarga reanudable; el plan vigente señala que faltan comprobaciones con poco espacio y cancelación durante transferencias grandes.

**Propuesta:** validar una instalación inicial con red interrumpida, reanudación, espacio insuficiente, cancelación y reintento. A partir de los resultados, mostrar antes de descargar el espacio requerido y disponible, explicar qué se conserva al reintentar y dar un paso siguiente claro cuando la descarga no pueda continuar.

**Implementado y probado en tests:** se enseña espacio libre y requerido antes de conectar, se puede cancelar sin borrar el `.part`, se reanuda por `Range` y ofrece reintentar; el SHA256 protege el archivo publicado. **Pendiente:** probar los casos de red/disco reales según la [matriz](validacion-release.md).

**Evidencia:** [requisitos de primera ejecución](../README.md) y el estado registrado en [el plan de auditoría](auditoria-plan-mejora-integral.md).

### P2 · Firmar y notarizar los binarios

**Hallazgo:** la guía informa que las descargas de Windows y macOS no están firmadas, por lo que el sistema operativo presenta avisos al abrirlas. Los hashes, la provenance y el SBOM ayudan a comprobar integridad y procedencia del release, pero no sustituyen una firma verificable por el sistema operativo.

**Implementado en CI, inactivo por defecto:** el workflow puede firmar ambos ejecutables de Windows con Authenticode y puede firmar/notarizar un `.app` de macOS y publicar un DMG cuando se activa `ENABLE_CODE_SIGNING` y están configuradas las credenciales necesarias. Los hashes se calculan después de la firma; SBOM y provenance siguen complementando la integridad. La lista de credenciales y operación queda en la [guía de firma](firma-releases.md).

**Pendiente de credenciales:** no se proporcionaron certificados a esta ejecución; el estado de Actions Secrets/Variables del repositorio remoto no es visible desde este checkout. La firma solo se activará cuando `ENABLE_CODE_SIGNING=true` y estén listos los secretos. La primera ejecución firmada deberá aprobarse con los verificadores de cada SO.

**Evidencia:** aviso de binarios sin firma en [guía de instalación](../dictado/README.md) y metadatos del release en el [changelog](../CHANGELOG.md).

### P1 · Soporte para Omarchy, Arch Linux y Hyprland/Wayland

**Hallazgo:** Omarchy usa Arch Linux y Hyprland sobre Wayland, con un flujo de trabajo centrado en el teclado. Instant documentaba Linux como compatible solo con X11: `pynput` no ofrece el hotkey global necesario en Wayland, el pegado dependía de `xdotool` y el instalador Linux usaba `apt-get`. En esa combinación fallaba el flujo principal de “mantener para dictar y soltar para pegar”.

**Implementado en código; soporte todavía experimental:**

- detectar atajos globales con el portal `GlobalShortcuts`, que comunica activación y liberación de la tecla; en Hyprland, integrar con el backend `xdg-desktop-portal-hyprland` y documentar cómo registrar el atajo;
- usar `wl-clipboard` para el portapapeles y `wtype` para enviar `Ctrl+V` en Wayland;
- adaptar el instalador y el diagnóstico para Arch/Omarchy y sus paquetes (`pacman`), conservando la ruta actual para Debian/Ubuntu;
- hay pruebas unitarias de descarga/diagnóstico/pegado y las instrucciones del primer registro; probar ventanas Wayland nativas y XWayland por separado en una sesión real.

**Criterio de cierre:** en una sesión real de Omarchy, mantener la tecla inicia la grabación, soltarla la detiene, la transcripción se pega en la aplicación enfocada y el portapapeles previo se restaura. El diagnóstico explica qué falta cuando no está disponible el portal o alguna herramienta. No requiere permisos de root ni lectura global de `/dev/input`.

**Evidencia:** el [manual de Omarchy](https://omarchy.org/manual/) describe su base Arch/Hyprland y su navegación con teclado; la [guía de Hyprland](https://wiki.hypr.land/configuring/core/binds/globals/) documenta los atajos globales y su integración D-Bus con XDPH; la especificación de [XDG GlobalShortcuts](https://flatpak.github.io/xdg-desktop-portal/docs/doc-org.freedesktop.portal.GlobalShortcuts.html) define eventos `Activated` y `Deactivated`; [wtype](https://github.com/atx/wtype) permite enviar `Ctrl+V` en Wayland y está disponible en [Arch Extra](https://archlinux.org/packages/extra/x86_64/wtype/). El estado actual de Instant está descrito en [la guía Linux](../dictado/README.md), [`hotkey.py`](../dictado/src/instant_app/hotkey.py), [`paste.py`](../dictado/src/instant_app/paste.py), [`deps.py`](../dictado/src/instant_app/deps.py) y [`install.sh`](../scripts/install.sh).

## Secuencia sugerida

1. **Validar la ruta implementada en Omarchy/Hyprland** con apps nativas y registrar cualquier excepción XWayland.
2. **Ejecutar la matriz de release instalada** en los sistemas soportados y reunir el corpus consentido para fijar el baseline de dictado.
3. **Completar lectores de pantalla y recorridos visuales** y configurar credenciales para una release firmada cuando existan.

## Alcance y límites

Este informe se actualizó el 11 de octubre de 2026 sobre el checkout `v0.5.0`. En esta intervención se implementaron cambios para Wayland/Omarchy, accesibilidad, escala y descarga, y se añadieron los harnesses, listas y CI de firma opcional. La suite, compilación, Pyflakes y parseo YAML pasaron. No están disponibles en este entorno certificados de firma, un equipo Omarchy ni instalaciones/lectores de pantalla de cada plataforma; esos resultados se mantienen como evidencia pendiente y no como trabajo realizado.
