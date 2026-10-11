# Revisión de accesibilidad de Instant

El panel incluye navegación por teclado, foco visible, regiones de estado,
controles con nombres y errores asociados, respeto por movimiento reducido,
estilos para contraste elevado/colores forzados y tamaños de interfaz al 100 %,
125 % y 150 %. Los objetivos interactivos principales tienen un mínimo de 44 px.

La interfaz web se puede revisar automáticamente con
`python -m unittest discover -s tests` desde `dictado/`. Los lectores de
pantalla y el tamaño visual final dependen del sistema operativo y deben
registrarse con la lista manual de abajo.

## Comprobaciones manuales

| Sistema y tecnología | Estado | Evidencia / versión |
|---|---|---|
| Windows + NVDA | Pendiente | |
| macOS + VoiceOver | Pendiente | |
| Linux + Orca (X11) | Pendiente | |
| Omarchy/Hyprland + lector disponible | Pendiente | |

Para cada sistema:

- [ ] Completar la configuración inicial solo con teclado; verificar orden de
  tabulación, retorno de foco y que ningún control quede atrapado.
- [ ] Navegar secciones, cambiar micrófono, ajustar tamaño y editar/eliminar un
  término de vocabulario.
- [ ] Confirmar que los lectores anuncian nombre, rol, valor, error y cambios
  de estado; no depender únicamente del color.
- [ ] Revisar foco visible, alto contraste y modo de colores forzados.
- [ ] Revisar el inicio, audio, configuración y vocabulario a 100 %, 125 % y
  150 %.
- [ ] Reducir la ventana hasta su mínimo admitido (760 × 480) y confirmar que
  se puede llegar a todos los controles sin recortes.
- [ ] Registrar lector/versión, sistema, resolución, escala y cualquier
  obstáculo; no marcar como verificado sin probarlo en la ventana real.
