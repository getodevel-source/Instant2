# Dise?o de UI y animaciones de Instant2

**Prop?sito:** que el estado del dictado se entienda de inmediato y que el movimiento corresponda a una operaci?n real.

## Direcci?n visual

Instant2 se presenta como una herramienta de voz, no como un panel t?cnico gen?rico. La ventana principal usa una base oscura petr?leo, texto claro y verde agua como se?al de voz. Los estados de espera, advertencia y error usan colores y etiquetas distintos. El overlay conserva una superficie oscura transl?cida para mantener contraste sobre otras aplicaciones y comparte los colores de marca.

Inicio muestra la tecla de dictado, el estado del micr?fono y el estado del servicio de lenguaje. Micr?fono, Ajustes y Vocabulario siguen accesibles desde la navegaci?n persistente. Los ajustes avanzados est?n agrupados para que el uso cotidiano no requiera conocer motores ni par?metros.

La tipograf?a usa la familia del sistema. Texto, controles y estados conservan contraste alto, foco de teclado visible y objetivos t?ctiles de al menos 44 px. El color no es la ?nica se?al.

## Jerarqu?a de Inicio

Inicio debe contestar en este orden:

1. **?Puedo empezar?** Mostrar micr?fono, tecla elegida y estado del reconocimiento.
2. **?Qu? servicio recibe el texto?** Indicar si el LLM configurado es local o remoto, el modelo cuando se conoce y el estado real. El audio va solamente al reconocedor local.
3. **?Qu? acci?n sigue?** Iniciar, detener, probar micr?fono o abrir los ajustes necesarios.

El servicio local debe comprobarse antes de mostrar ?Listo?. El modelo de voz y el LLM tienen estados separados. El instalador no incluye modelos de lenguaje; el runtime y el modelo se configuran fuera del instalador.

## Movimiento por estado

Las animaciones tienen una causa observable y un estado final estable. El movimiento usa `transform` y `opacity` con curvas breves. No se animan dimensiones de layout, sombras grandes ni desenfoques.

| Evento real | Respuesta visual | Duraci?n objetivo | Movimiento reducido |
|---|---|---:|---|
| Abre o cambia una secci?n | El contenido aparece con un desplazamiento vertical corto; la navegaci?n cambia ?nfasis | 220?280 ms | Mostrar directamente en su posici?n final |
| Abre configuraci?n desde Inicio | Los ajustes se despliegan y el foco va al primer campo incompleto | 180 ms | Mostrar de inmediato y conservar el foco |
| Mantiene la tecla y habla | El overlay indica ?Escuchando?; el espectro sigue el nivel real del micr?fono | M?ximo 30 fps | Se?al est?tica sin respiraci?n, giro ni pulso |
| Reconoce o corrige texto | El overlay cambia a ?Transcribiendo? o ?Revisando texto? | Hasta el final o timeout real | Texto de estado est?tico |
| Pega el resultado | Confirmaci?n o error textual; no afirma que el campo receptor lo acept? | 140?200 ms | Cambio instant?neo |
| El servicio falla o se recupera | Aviso claro y cambio solo cuando cambia el estado real | Una transici?n | Sin pulso peri?dico |

Curva principal: `cubic-bezier(.2,.8,.2,1)`. Presionar un bot?n puede escalar hasta `0.985` mientras se mantiene. No hay part?culas, parallax, ?latido de IA? ni loaders que sigan girando despu?s de terminar la operaci?n.

## Responsabilidad de cada superficie

- **Panel:** controles, opciones, estados persistentes y ayuda. El cambio de secci?n y las respuestas de los controles usan transiciones cortas; el reposo no tiene animaciones en bucle.
- **Overlay:** una se?al compacta de escucha, transcripci?n, correcci?n y entrega. Durante la escucha usa datos reales de audio. Permanece sin foco para no sacar al usuario del campo donde dicta.
- **Mensajes:** describen el resultado real. ?Pegado enviado? significa que Instant2 envi? la combinaci?n de teclas, no que haya le?do y verificado el contenido del campo destino.

## Accesibilidad y rendimiento

- Respetar `prefers-reduced-motion` tanto en CSS como en el renderizador Canvas. En reposo, detener el ciclo de dibujo; con movimiento reducido, redibujar al cambiar datos reales.
- Mantener navegaci?n completa con teclado, foco visible, `aria-live` para cambios de estado y contraste en temas de alto contraste y colores forzados.
- El Canvas se limita a 30 cuadros/s durante escucha y transiciones; se detiene al descansar.
- El espectro reutiliza los datos de audio disponibles; no crea otra captura ni guarda muestras.
- Los tiempos de carga muestran estados reales, sin porcentajes inventados.

## Criterios visuales

- Una captura de 1100 ? 600 permite identificar el atajo, micr?fono y estado del servicio desde Inicio.
- El estado del reconocimiento y el estado del LLM son distinguibles sin depender solo del color.
- A 100 %, 125 % y 150 % se leen t?tulos y se pueden usar los controles principales.
- El movimiento reducido conserva la misma informaci?n y elimina movimientos no esenciales.
- Los estados no aparecen como listos antes de que el servicio confirme que responde.

## Validaci?n manual pendiente

La captura de p?ginas en Windows se puede automatizar con `dictado/bench/web_panel_smoke.py`; el overlay y el pegado requieren probarse en campos reales. Wayland/Omarchy requiere comprobar el atajo global, la ventana sin foco y el pegado en el compositor.
