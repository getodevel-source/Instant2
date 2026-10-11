# Plan de mejora integral de Instant

> **Documento histórico.** Esta auditoría conserva sus puntajes, ejemplos y
> estado de trabajo como una foto anterior a `v0.5.0`; menciones como “el
> actualizador no inicia la descarga” describen ese momento y no el estado
> actual del producto. Para pendientes vigentes y evidencia posterior, usar
> el [informe de mejoras actualizado](informe-mejoras.md), la
> [lista de validación de releases](validacion-release.md) y el registro de
> [accesibilidad](accesibilidad.md).

Este plan convierte la auditoría del repositorio en cambios revisables. La primera meta es que el panel y el overlay de voz se sientan claros, fluidos y confiables; después se corrige cada proceso del producto, empezando por la actualización que hoy detecta versiones pero no inicia sola la descarga.

## Ruta de trabajo

1. **Panel y animación de voz** — rediseñar la navegación, jerarquía visual, estados, feedback y accesibilidad del panel; mejorar la animación del overlay usando nivel, espectro y tono reales del micrófono. Mantener bajo el consumo en reposo y el costo del render.
2. **Actualizaciones** — iniciar la descarga verificada al detectar una versión nueva, mostrar progreso y errores en el panel, y resolver la instalación por plataforma con una acción final visible que no corte un dictado sin aviso.
3. **Captura y dictado** — revisar tecla, ciclo de micrófono, pre-roll, VAD, fragmentación, reintentos y latencia. Medir con entradas reproducibles y separar fallos de audio de errores del reconocedor.
4. **Corrección y pegado** — auditar perfiles de vocabulario, coincidencia fonética, pulido LLM opcional, portapapeles y envío de teclas en los tres sistemas.
5. **Configuración y ciclo de vida** — recorrer instalación inicial, descarga de modelos, permisos/dependencias, autostart, bandeja, instancia única, inicio, cierre, reinicio y desinstalación.
6. **Distribución y cierre** — revisar build, assets/sidecars, releases, instalación limpia y actualización real por plataforma; cerrar brechas de documentación y actualizar las capturas cuando la UI cambie.

Cada etapa se cierra con evidencia concreta: pruebas automatizadas para la lógica, smokes de la UI/paquete donde correspondan y una lista corta de hallazgos abiertos. Los puntajes se vuelven a estimar al cerrar cada etapa.

## Puntaje inicial por proceso

Escala de capacidad actual observada en el repo: **1** = falla o falta; **5** = funciona con fricción o cobertura limitada; **10** = flujo confiable, claro y comprobado en sus plataformas objetivo. El puntaje no representa satisfacción de usuarios ni una garantía estadística.

| Proceso | /10 | Evidencia y motivo | Prioridad |
|---|---:|---|---|
| Panel de ajustes y navegación | 4 | Todos los controles están disponibles, pero viven en una página larga con navegación por scroll y jerarquía visual básica. | P0 |
| Overlay y animación de voz | 6 | Recibe nivel, nueve bandas y tono reales; se pausa en reposo. El dibujo actual tiene estados y una base reactiva, pero el resultado visual y el feedback de transcripción todavía pueden subir mucho. | P0 |
| Tecla global y ciclo de dictado | 8 | Hay captura, normalización y pruebas de transiciones, límites de sesión y flancos tardíos. Falta validar el recorrido completo en equipos y aplicaciones reales. | P1 |
| Micrófono y captura de audio | 7 | Resuelve alias/backends y fallback del mismo dispositivo; hay pruebas de WASAPI, estéreo, callback y medidor. El hardware real sigue siendo variable. | P1 |
| VAD, segmentación y reconocimiento | 7 | Parakeet/VAD offline, recuperación de fragmentos vacíos y fallback de señal débil; la evidencia de precisión documentada usa un corpus pequeño y voces sintéticas. | P1 |
| Vocabulario y corrección local | 7 | Perfiles y reemplazos exactos/fonéticos con protecciones para palabras comunes; revisar ergonomía, casos límite y vocabularios grandes. | P1 |
| Pulido LLM opcional | 7 | Apagado por defecto, conserva la salida local si falla y rechaza cambios de palabras; falta más evidencia de calidad con dictados naturales. | P2 |
| Pegado y portapapeles | 5 | Hay rutas por SO, pero dependen de foco, permisos, X11/herramientas y temporización; cubrir mejor fallos y destinos reales. | P1 |
| Descarga y verificación de modelos | 7 | Descarga por partes, reanudación/verificación y mensajes de progreso; auditar red inestable, espacio, cancelación y primera ejecución limpia. | P1 |
| Persistencia de configuración | 7 | Hay respaldo `.bak`, pero el JSON se escribía directamente y podía quedar truncado si el proceso se interrumpía durante el guardado. | P1 |
| Instalación y dependencias | 7 | El setup continúa si faltan componentes y da pistas; revisar el mínimo real de Python y una instalación limpia en cada plataforma. | P1 |
| Actualización de la aplicación | 3 | El chequeo silencioso solo cambia el texto del botón. Descargar y aplicar requieren pasos separados; la descarga del panel no publica progreso. | P0 |
| Inicio automático, bandeja e instancias | 7 | Rutas por SO y pruebas de idempotencia/ciclo de vida; la cantidad de casos de procesos hace necesario probar builds instalados. | P1 |
| Diagnóstico y manejo de errores | 7 | Logs rotativos, diagnóstico del panel y mensajes accionables en varios fallos; revisar que errores silenciosos de red y UI tengan salida visible. | P2 |
| CLI y automatización | 8 | `setup`, `run`, `stop`, `check`, `status` y modo no interactivo documentados y probados. | P2 |
| Build, instaladores y releases | 7 | CI para Windows/Linux/macOS, smoke de binarios congelados y publicación con SHA256; faltan pruebas de extremo a extremo de actualización/rollback en release instalada. | P1 |
| Privacidad y operación local | 9 | Audio/modelo corren localmente; LLM externo es opcional y apagado por defecto. Descargas de modelos/releases sí necesitan red. En el cierre se detectó y retiró el texto reconocido del log. | P2 |
| Documentación y onboarding | 8 | README, guías de motores, diagnóstico y decisiones de UI cubren bien los flujos; capturas y detalles operativos deben seguir el rediseño y el updater. | P2 |

## Puntaje revisado y estado de trabajo

La puntuación inicial queda arriba como referencia. Después de implementar y comprobar las primeras áreas, la evaluación vigente es:

| Proceso | Inicial | Actual | Evidencia y límite actual |
|---|---:|---:|---|
| Panel de ajustes y navegación | 4 | 8 | Interfaz rehecha con navegación lateral, jerarquía, estados y ajustes avanzados; render QtWebEngine a 100 %. Falta validar usabilidad con personas y escalas de pantalla variadas. |
| Overlay y animación de voz | 6 | 7 | El estilo de espectro ahora vive en una cápsula compacta; sus 21 barras interpolan las nueve bandas reales y el nivel solo conserva una señal mínima. Escucha, transcripción, confirmación y avisos comparten la misma composición. El smoke web renderiza a 100 %; falta validar con micrófono físico y en la instalación actualizada. |
| Tecla global y ciclo de dictado | 8 | 8 | Captura/normalización y transiciones cubiertas; se corrigió el borde de inicio de sesión del pre-roll. Falta el recorrido con apps reales y teclas elevadas. |
| Actualización de la aplicación | 3 | 7 | Detección diaria, descarga SHA256 en segundo plano, progreso, retry, confirmación visible y cierre real del panel antes de reemplazar el portable; build/smoke congelado Windows. Falta actualizar una instalación real de cada plataforma. |
| Micrófono y captura de audio | 7 | 8 | La entrada en pre-roll y cola activa ahora tiene un borde atómico para no repetir bloques al iniciar una sesión; las pruebas de callbacks/fallback siguen verdes. Falta probar sesiones largas en hardware y drivers distintos. |
| VAD, segmentación y reconocimiento | 7 | 7 | El repo cubre fallback y métricas con audio reproducible; no hay validación nueva de precisión con voces y micrófonos reales. |
| Vocabulario y corrección local | 7 | 8 | Los alias de varias palabras ya no cruzan comas ni consumen puntuación. Las reglas fonéticas y lista de protección siguen cubiertas; falta revisar comodidad con perfiles grandes y dictados espontáneos. |
| Pulido LLM opcional | 7 | 7 | Se conserva la salida local si el endpoint falla o cambia palabras; la UI aclara que un remoto recibe texto más vocabulario. Falta verificar varios servidores compatibles y latencia real. |
| Pegado y portapapeles | 5 | 5 | Se mantienen rutas por sistema y avisos de dependencia; no hubo pegado físico en destinos reales. Linux documenta el soporte X11 y no implementa Wayland. |
| Descarga y verificación de modelos | 7 | 8 | El requisito de espacio ahora descuenta bytes presentes en archivos `.part` reanudables. La transferencia completa sigue protegida por hash; falta probar falta de espacio real y cancelación durante descargas grandes. |
| Diagnóstico y manejo de errores | 7 | 7 | Varios errores de red, audio y configuración se muestran al usuario; aún falta revisar cada fallo y confirmar ayuda accionable de extremo a extremo. |
| CLI y automatización | 8 | 8 | Comandos y escenarios de ciclo de vida tienen cobertura automatizada; no se hizo una ejecución nueva de cada opción manual. |
| Persistencia de configuración | 7 | 8 | Se serializa, sincroniza y reemplaza atómicamente el JSON; si un archivo heredado está dañado, recupera el `.bak`. Hay pruebas para ambos casos; falta ejercitar fallos de disco/permisos reales. |
| Instalación y dependencias | 7 | 8 | El diagnóstico coincide con Python 3.12 mínimo y no intenta instalar paquetes con un intérprete incompatible. La instalación limpia multiplataforma sigue pendiente. |
| Inicio automático, bandeja e instancias | 7 | 8 | El portable POSIX ahora apunta a su ejecutable congelado. La creación/limpieza se prueba sobre archivos aislados; falta confirmación de sesión de inicio real en Linux/macOS. |
| Privacidad y operación local | 9 | 9 | Daemon, motor, corrector y errores de respuesta LLM no escriben texto reconocido en logs. UI/README aclaran que el endpoint remoto recibe texto y vocabulario activo; también documentan `DICTADO_SAVE_WAVS`. LLM continúa apagado por defecto. |
| Build, instaladores y releases | 7 | 7 | CI incorpora smoke del portable Windows y empaqueta el helper; quedan los runners Linux/macOS y un upgrade/rollback instalado real. |
| Documentación y onboarding | 8 | 8 | README, changelog, capturas y privacidad acompañan la UI nueva y los cambios revisados; falta validar instalación limpia y rutas de upgrade en cada SO. |

**Fase 1 — UI y voz: cerrada en código y smoke, falta validación instalada.** Se corrigió además un fallo del puente QWebChannel (las acciones web llamaban una señal Qt como función) que dejaba sin funcionar el panel. El medidor consume el nivel real del backend; el overlay usa una cápsula reactiva a las nueve bandas, reemplaza las órbitas decorativas por una onda legible y mantiene la tecla y los estados de dictado en la misma composición. El smoke visual muestra escucha, transcripción, confirmación y aviso; falta probar la nueva versión con voz y en una instalación actualizada.

**Fase 2 — actualizaciones: implementación publicada; recorrido instalado pendiente.** La release `v0.3.2` ya publica instalador, portable y hashes tras pasar suites y builds de Windows, macOS y Linux. En este equipo, la instalación `0.3.1` sigue abierta y su último chequeo automático fue antes de esa publicación; la ventana nativa no está disponible para forzar «Buscar actualizaciones» ni «Actualizar y reiniciar» desde esta sesión. Por eso todavía no se confirmó la descarga y aplicación de la release en esa instalación. El flujo necesita ese chequeo desde la app y un reinicio de actualización para cerrar la auditoría.

**Fase 3 — captura y ciclo de dictado: en curso.** Se cerró una carrera entre el pre-roll y la cola viva que podía repetir los mismos bloques al comienzo de una sesión. El hint del panel ahora refleja la tecla global elegida, y una URL LLM malformada da aviso visible en vez de quedar solo en el log. No hice una grabación real en este equipo.

**Fase 4 — corrección y pegado: en curso.** Se corrigió un alias de varias palabras que podía consumir una coma. Se hizo explícito que un endpoint remoto recibe el texto y el vocabulario activo, se quitaron las transcripciones de los logs de las cuatro capas y se documentó el guardado opt-in de audio fallido. Resta comprobar pegado en destinos reales de cada SO y cubrir fallos de permisos/foco.

**Fase 5 — configuración y ciclo de vida: en curso.** El guardado reemplaza atómicamente el JSON y recupera el `.bak` si encuentra una config heredada corrupta; el autostart POSIX de los portables apunta al binario congelado; el diagnóstico alinea el mínimo de Python con el paquete y evita una instalación incompatible. Resta pasar por una instalación limpia, arranque de sesión y cierre en Linux/macOS/Windows instalados.

**Fase 6 — distribución y cierre: en curso.** La CI suma un smoke del portable Windows con QtWebEngine y el bundle contiene el helper de update. Resta dejar que corran los tres runners de CI y ejecutar upgrades/rollback con una release instalada.

La auditoría sigue abierta: estos avances no certifican el audio físico, pegado en apps de terceros, instalación limpia ni upgrade real.

## Condiciones de cierre global

- [x] El panel permite encontrar y completar cada ajuste con navegación visible y controles agrupados por sección.
- [x] El overlay expresa inicio, voz, procesamiento, copia y error con datos reales y sin animar en reposo.
- [ ] Cada control muestra qué está haciendo, qué terminó y qué hacer si falla.
- [ ] Los recorridos de actualización y configuración inicial se han comprobado más allá de mocks.

> **Nota de producción (docs):** no se marcan estas dos casillas aunque el
> código de errores accionables y del updater ya esté: falta validación con
> hardware real (micrófono físico, pegado en apps de terceros, instalación
> limpia y upgrade instalado por SO). Sin eso, marcarlas sería mentir.
- [x] Las correcciones respetan el modo local por defecto y no alteran texto por inferencia sin configuración explícita.
- [x] Suite, smokes de las dos superficies web y build/smoke del portable Windows quedan en verde; build multiplataforma y actualizaciones instaladas siguen pendientes.
