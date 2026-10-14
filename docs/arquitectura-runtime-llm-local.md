# Ciclo de vida de modelos y recursos en Instant2

Instant2 deja el motor de voz y el LLM bajo un ?nico proceso de dictado persistente. El panel muestra su estado y env?a reintentos; cerrar la ventana del panel no detiene el dictado ni apaga el LLM que sigue atendi?ndolo.

## Uso r?pido

En **Ajustes ? Voz, rendimiento y privacidad avanzada ? Prioridad de recursos** eleg?:

| Perfil | Comportamiento en reposo | Para qui?n |
|---|---|---|
| M?xima rapidez | Mantiene ASR, Kokoro y el LLM cargados mientras el proceso siga activo. | Equipos con memoria disponible y dictado frecuente. |
| Equilibrado (predeterminado) | Libera los modelos de voz y duerme el LLM tras 5 minutos sin actividad. | Uso general; conserva la pol?tica previa de descarga de ASR. |
| Menor uso de memoria | Libera los modelos tras 90 segundos. | Equipos con poca memoria o muchas aplicaciones abiertas. |

El pr?ximo inicio del proceso de dictado aplica los cambios del perfil. El modelo GGUF y el runtime se instalan por separado; Instant2 no los descarga ni los incluye en el instalador.

## Qui?n administra cada modelo

| Componente | Proceso propietario | Carga y descarga |
|---|---|---|
| Parakeet y VAD | Daemon de dictado | Se preparan al iniciar y se liberan seg?n el perfil cuando F9 queda inactivo. |
| llama.cpp administrado | Daemon de dictado | Se inicia en segundo plano con los archivos elegidos. Si la versi?n admite `--sleep-idle-seconds`, se libera el modelo en reposo; la siguiente solicitud lo despierta. |
| LLM externo local o remoto | El proveedor configurado | Instant2 env?a solo texto. No intenta iniciar ni detener el servicio externo. La UI indica si el destino es local o remoto. |
| Kokoro, F10 | Daemon de dictado | Se carga al primer uso y se libera seg?n el perfil. |
| Kokoro, lector del panel | Proceso del panel | Se carga al iniciar una lectura y se libera seg?n el mismo perfil. |

El panel no conserva un supervisor paralelo. Lee un estado peque?o, sin texto dictado ni prompts, que el daemon actualiza en la carpeta privada de configuraci?n. El bot?n de reintento manda una se?al local al daemon.

## Camino de un dictado con correcci?n LLM

1. El audio pasa al reconocedor local existente.
2. Instant2 aplica sus correcciones locales.
3. Si la correcci?n LLM est? habilitada, el daemon espera hasta 15 segundos a que termine el primer arranque del servicio administrado.
4. El texto se env?a al endpoint OpenAI compatible que ya est? configurado.
5. Si el LLM est? dormido, llama.cpp recibe hasta 60 segundos para despertar y cargar el modelo.
6. Si el servicio falla o no termina de iniciar, se pega la transcripci?n corregida localmente. Dictado normal sigue disponible.

El LLM no recibe audio. Las solicitudes y respuestas de usuario no se escriben en los registros ni en el archivo de estado. La verificaci?n de significado del dictado conserva su l?mite corto para que una respuesta lenta no bloquee la sesi?n.

## Arranque, GPU y memoria

- llama.cpp se liga a loopback; Instant2 nunca abre el servidor a la red local.
- Se consulta el runtime instalado para detectar dispositivos y memoria libre. El runtime intenta el dispositivo acelerado utilizable y hace un ?nico fallback a CPU si falla.
- Antes de intentar CPU, Instant2 estima el espacio necesario para el GGUF, el contexto y un margen para el escritorio. Es una advertencia preventiva, no una garant?a de carga.
- Se usa una sola solicitud en paralelo y contexto de 4096 tokens para acotar memoria.
- Las versiones de llama.cpp con `--sleep-idle-seconds` pueden liberar los pesos sin cerrar el proceso. Versiones antiguas y proveedores externos conservan su propio comportamiento.

El primer dictado posterior a una descarga puede tardar m?s: Parakeet se vuelve a cargar y, si corresponde, el LLM despierta desde reposo. En el perfil de m?xima rapidez se prioriza la latencia; en el de memoria se acepta ese arranque en fr?o.

## Qu? medir en cada iteraci?n

Para comparar cambios, registrar por separado:

- latencia fr?a del primer dictado despu?s del arranque;
- latencia caliente p50/p95 de ASR y de la correcci?n LLM;
- RAM y VRAM al iniciar, durante inferencia y en reposo;
- tiempo de despertar del LLM dormido y tasa de fallback local;
- cortes de Kokoro durante lectura larga;
- comportamiento en CPU sin GPU y con 8 GB de RAM.

Los benchmarks sint?ticos actuales ayudan a comparar versiones, pero no sustituyen las pruebas manuales en Windows de usuario ni en Wayland/Omarchy. No se debe reiniciar o cargar otro modelo en una sesi?n activa para medirla sin coordinarlo con la persona que est? usando Instant2.
