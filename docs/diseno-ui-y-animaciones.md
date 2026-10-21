# Diseño de UI y movimiento de Instant2

## Dirección visual

La interfaz mantiene el contexto de una herramienta local de voz: base petróleo,
verde agua para las acciones y señales de voz, y estados acompañados por texto.
La composición usa superficies planas, tipografía Manrope incluida localmente,
iconos de trazo fino y una escala coherente de espaciado, controles y radios.
No necesita descargar fuentes ni bibliotecas de animación al abrirse.
Los compositores inyectan los colores del panel y del indicador desde
`branding.PALETTE`; las páginas no mantienen copias manuales de la paleta.
El símbolo de la app es la señal de tres barras del chip del panel; el sistema
completo (logotipo, iconos, área libre y usos) está en
[`design.md`](../design.md).

Las cinco vistas conservan sus controles y operaciones por QWebChannel:

- **Inicio:** estado del dictado, iniciar/detener, atajo elegido, ayuda de F10,
  reconocimiento, LLM, micrófono y comprobaciones para empezar. Los detalles
  del equipo son filas de información; el estado y las acciones tienen prioridad.
- **Micrófono:** selector, actualización de dispositivos, prueba y nivel real.
- **Ajustes:** atajos, inicio con el sistema y escala. Los ajustes avanzados
  conservan rendimiento, privacidad, runtime, credenciales y actualizaciones.
- **Vocabulario:** perfiles y edición de términos, variantes y fonética.
- **Leer texto:** editor, contador, Dora, velocidad, estado, progreso,
  reproducción y cancelación. En ventanas amplias, las opciones están junto
  al editor; con poco ancho o zoom, se ubican debajo.

Los avisos transitorios forman parte del contenido, debajo de la barra superior.
Conservan sus acciones y duración, y no se superponen a los controles. Los modales
mantienen navegación de teclado, Escape y restauración del foco.

## Indicadores de F9 y F10

Se conserva el comportamiento: mantener F9 para dictar, soltar para procesar;
presionar F10 para leer la selección y otra vez para detenerla. El atajo de
dictado sigue siendo configurable.

Los dos estilos existentes comparten una cápsula de 344 × 56 px durante el uso
activo. `classic` muestra nueve barras; `orbital`, diecisiete bandas interpoladas.
La señal sigue el micrófono o la salida de Kokoro. Los iconos, el estado y el atajo
distinguen dictado y lectura. Los avisos amplían la superficie hasta 440 px de
ancho y ajustan el texto en hasta cuatro líneas, sin comprimirlo en una sola.

El indicador se ancla abajo al centro, a 16 px del límite del área de trabajo,
con un margen interno de 12 px. La ventana no toma foco y permite que los clics
lleguen a la aplicación que está debajo.

| Cambio | Respuesta | Tiempo |
|---|---|---:|
| Navegar | Opacidad y desplazamiento de 5 px | 200 ms |
| Abrir un modal | Opacidad y desplazamiento breve | 180 ms |
| Desplegar opciones | Opacidad y desplazamiento de 4 px | 160 ms |
| Mostrar el indicador | Opacidad, escala 0,99 → 1 y desplazamiento de 3 px | 180 ms |
| Cambiar estado de voz | Superficie y textos estables, asentamiento del icono | 120 ms |
| Ocultar el indicador | Opacidad y desplazamiento de 3 px | 130 ms |
| Recibir audio | Ataque y caída suavizados de bandas reales | 45 / 120 ms |

Las entradas y salidas interrumpidas retoman la opacidad actual, sin reiniciar
desde cero. Los cambios de estado muestran su etiqueta inmediatamente; solo
el icono existente pasa de escala 0,94 a 1 con desaceleración, sin rebote.
Estos tiempos son de presentación y no modifican la permanencia de los estados
ni los eventos que los activan.

La referencia [Motion Framer](https://github.com/freshtechbro/claudedesignskills/blob/main/.claude/skills/motion-framer/SKILL.md)
orientó el movimiento por estado y la continuidad de entrada/salida. La
implementación usa CSS y Canvas de la arquitectura existente. La skill
`high-end-visual-design` se aplicó a composición, tipografía, consistencia y
revisión final, adaptando sus recomendaciones a una herramienta de escritorio.

## Accesibilidad y rendimiento

- `prefers-reduced-motion` elimina entradas, desplazamientos y giros. La señal
  de audio permanece estática; solo los cambios de contenido o tamaño requieren
  un redibujo. La preferencia puede cambiar con el indicador visible o inactivo.
- El Canvas anuncia estados en una región `aria-live`; no es la única fuente
  de información. Los colores se acompañan de iconos y etiquetas.
- El dibujo se detiene al quedar inactivo, incluso con un paquete de audio tardío.
- El Canvas usa `requestAnimationFrame` mientras hay movimiento visible. También
  se detiene en confirmaciones, avisos, errores y señal de audio ya estabilizada;
  las nuevas muestras reanudan la interpolación. Solo el giro de procesamiento
  requiere dibujo continuo mientras ese estado permanezca visible.
- Los medidores usan transformaciones de escala, sin animar dimensiones de layout.
- Se mantienen foco visible, colores forzados y preferencia de mayor contraste.
- Las consultas de contenedor adaptan la composición al ancho efectivo con zoom.

## Verificación del rediseño

El 14 de octubre de 2026 se revisó en Windows el host QML/QtWebEngine real:

- 17 capturas del panel: las cinco vistas, lectura activa/error, captura de tecla,
  ajustes avanzados, vocabulario vacío, modelo ausente, zoom 125/150 % y tamaño
  mínimo 760 × 480. Sin desbordamiento horizontal ni errores JavaScript detectados.
- 20 capturas del overlay por cada preferencia de movimiento (40 en total):
  ambos estilos y estados de dictado, procesamiento, lectura, parada, éxito,
  aviso y error. Se verificó la señal reactiva/estática y la parada del renderizador.
- 110 pruebas `unittest` del panel, overlay, ajustes, ciclo de vida, Kokoro y F10,
  junto con las comprobaciones de regresión de audio, hotkeys y pegado existentes.
- Tras las ocho mejoras se repitieron esas capturas y la suite completa:
  376 pruebas correctas y seis omitidas. La evidencia de motores reales,
  rendimiento y distribución está en [la auditoría](auditoria-ocho-mejoras.md).
- Manrope y su licencia se incluyen en setuptools, el build local y las tres
  recetas de distribución de GitHub Actions.

Las capturas e informes locales están en `.ui-review/` (ignorado por Git).
Las verificaciones usan datos controlados para no modificar ajustes personales.
No equivalen a una sesión de dictado humano, una prueba de selección y lectura
en cada aplicación externa, ni una validación de Linux/Wayland/macOS. El respaldo
Tk conserva su comportamiento previo cuando QtWebEngine no está disponible.

## Verificación del ajuste de movimiento de F9/F10

El ajuste posterior, limitado al Canvas del indicador, se revisó en el host real
QtWebEngine de Windows: 12 estados visibles por cada estilo, más inactividad,
con movimiento normal y reducido (52 capturas). Se comprobaron interrupciones
de entrada/salida, reaparición durante una salida, cambio de preferencia de
movimiento en actividad/inactividad y suspensión del dibujo al estabilizarse.
Los paquetes de audio no provocaron redibujos en modo reducido ni tras ocultarse.

La comparación con la versión anterior produjo imágenes idénticas píxel a píxel
para éxito, aviso, error e inactividad de F9 y F10 en ambos estilos (14 pares).
La suite completa terminó con 376 pruebas, seis omitidas y ningún fallo;
incluye las regresiones de atajos, secuencias del daemon y lectura/cancelación F10.
También pasaron la comprobación de nombres de Python y el arranque de la CLI.

La evidencia local está en `.ui-review/motion-f9-f10/`. Las muestras de audio y
los eventos de la revisión visual fueron controlados. El modo reducido se validó
forzando la preferencia de Chromium y ejercitando su manejador de cambio, sin
alterar la configuración del sistema. Esta revisión no cubre dictado humano,
selección en aplicaciones externas, el respaldo Tk ni otros sistemas operativos.
