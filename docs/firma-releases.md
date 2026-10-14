# Firma de releases

El workflow admite firma como función opt-in. Sin configuración, Windows y
macOS siguen publicándose con los hashes existentes y sin firma de sistema
operativo. Para activar la ruta completa hay que configurar **la variable de
repositorio** `ENABLE_CODE_SIGNING` con valor `true` y los secretos siguientes
en Settings → Secrets and variables → Actions:

| Nombre | Contenido |
|---|---|
| `WINDOWS_SIGNING_PFX_BASE64` | Certificado Authenticode de publicación `.pfx`, codificado en Base64. |
| `WINDOWS_SIGNING_PFX_PASSWORD` | Contraseña del PFX. |
| `APPLE_DEVELOPER_ID_P12_BASE64` | Certificado Developer ID Application exportado como `.p12`, codificado en Base64. |
| `APPLE_DEVELOPER_ID_P12_PASSWORD` | Contraseña del `.p12`. |
| `APPLE_DEVELOPER_IDENTITY` | Identidad completa que muestra `security find-identity -v -p codesigning`. |
| `APPLE_ID` | Apple ID usado para notarización. |
| `APPLE_APP_SPECIFIC_PASSWORD` | Contraseña específica para apps de Apple. |
| `APPLE_TEAM_ID` | Team ID de Apple Developer. |

Cuando se activa, el workflow falla de forma explícita si falta un secreto. En
Windows firma `Instant2.exe` y `Instant2-Setup.exe`, aplica timestamp RFC 3161 y
verifica Authenticode. En macOS PyInstaller firma sus componentes durante la
construcción con Developer ID; el paso de release verifica el ejecutable,
firma el bundle `.app` con hardened runtime, crea `Instant2-macos.dmg`, solicita
notarización, adjunta el ticket (staple) y evalúa el bundle y el DMG. Los
SHA256 se producen después de firmar/notarizar; el binario `instant2-macos`
conserva el nombre que consume el actualizador.

El certificado privado se decodifica en el runner temporal y se elimina al
terminar el paso. Los secretos no deben ponerse en variables públicas, archivos
del repositorio, logs ni argumentos de comandos manuales. Para renovar o
revocar un certificado, actualizar el secreto correspondiente antes del
siguiente tag; no publicar una release hasta que la verificación del paso de
firma pase.

## Verificación local

Windows (SDK de Windows):

```powershell
signtool verify /pa /v Instant2.exe
signtool verify /pa /v Instant2-Setup.exe
```

macOS:

```bash
codesign --verify --deep --strict --verbose=2 Instant2.app
xcrun stapler validate Instant2-macos.dmg
spctl --assess --type open --context context:primary-signature \
  --verbose=2 Instant2-macos.dmg
```

La firma identifica al publicador y detecta cambios en el artefacto firmado;
SHA256 permite comprobar que la descarga coincide con el sidecar. La firma no
garantiza que el programa no tenga defectos, y ni el sidecar ni la firma
sustituyen las atestaciones de build o el SBOM.
