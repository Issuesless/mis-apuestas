# Mi Bank de Apuestas · puesta en marcha

Una web tuya, gratis, que abres igual en el móvil y en el ordenador, con tus apuestas sincronizadas
y un PDF por email el día 1 de cada mes. No depende de Claude: funciona aunque no pagues nada.

**Tiempo: unos 15 minutos, una sola vez.** Después solo usas la web.

| Servicio | Para qué | Coste |
|---|---|---|
| Supabase | Base de datos y tu inicio de sesión | Gratis (plan Free) |
| GitHub | Guarda el código, publica la web y lanza el informe mensual | Gratis |
| Gmail | Envía el PDF a tu correo | Gratis |

## Qué hay en esta carpeta

- `docs/` la web (lo que ves en el móvil y en el ordenador)
- `supabase/schema.sql` crea las tablas y la seguridad
- `informe/` genera el PDF mensual y lo envía
- `.github/workflows/` las dos tareas automáticas (informe mensual y «mantener activa» la base de datos)

> Si en tu ordenador no ves `.github`: en Mac pulsa Cmd + Mayús + punto en Finder para mostrar carpetas ocultas.

---

## Paso 1 · Base de datos (Supabase)

1. Entra en **supabase.com** → *Start your project* y crea una cuenta.
2. *New project*: nombre `mis-apuestas`, plan **Free**, región de Europa (por ejemplo Frankfurt) y una contraseña de base de datos (guárdala; no la vas a necesitar en el día a día).
3. Cuando termine de crearse: **SQL Editor** → *New query* → pega todo el contenido de `supabase/schema.sql` → **Run**. Debe decir «Success».
4. **Authentication → Users → Add user → Create new user**: pon tu correo y una contraseña, y marca *Auto Confirm User*. Esa es la cuenta con la que entrarás a la web.
5. **Authentication → Sign In / Providers**: desactiva *Allow new users to sign up*, para que nadie más pueda crearse una cuenta.
6. Apunta tres datos (en **Settings → API Keys**, o en el botón **Connect**):
   - **Project URL** (algo como `https://abcdefgh.supabase.co`)
   - **Publishable key** (empieza por `sb_publishable_`; en proyectos antiguos se llama *anon key*). Es pública, no pasa nada por ponerla en la web.
   - **Secret key** (empieza por `sb_secret_`; en proyectos antiguos *service_role*). **Esta sí es secreta**: no la pegues en la web ni la compartas.

## Paso 2 · Conectar la web con tu base de datos

Abre `docs/config.js` y pon tu Project URL y tu Publishable key entre las comillas. Si ya has subido el código a GitHub (paso 3), puedes editarlo allí mismo: abre `docs/config.js` → icono del lápiz → pega → *Commit changes*.

## Paso 3 · Subir el código a GitHub

1. Crea una cuenta en **github.com** si no la tienes.
2. **New repository**: nombre `mis-apuestas` y márcalo como **Public** (las páginas web gratuitas de GitHub solo funcionan con repositorios públicos). Que sea público no expone tus datos: el repositorio solo contiene código; tus apuestas están en Supabase, protegidas por tu inicio de sesión.
3. En el repositorio nuevo: **Add file → Upload files** y arrastra **todo el contenido** de esta carpeta (incluida `.github`). Pulsa *Commit changes*.

## Paso 4 · Publicar la web

1. En el repositorio: **Settings → Pages**.
2. *Source*: **Deploy from a branch** · *Branch*: `main` · carpeta `/docs` → **Save**.
3. En 1–2 minutos aparece tu dirección: `https://TU-USUARIO.github.io/mis-apuestas/`. Ábrela, entra con el correo y la contraseña del paso 1 y ya funciona.
4. **En el móvil**: abre esa dirección y usa *Compartir → Añadir a pantalla de inicio* (iPhone) o el menú del navegador → *Instalar app / Añadir a pantalla de inicio* (Android). Se abre como una app.

## Paso 5 · Informe PDF mensual por email

1. En tu cuenta de Google activa la **verificación en dos pasos** y crea una **contraseña de aplicación** (Cuenta de Google → Seguridad → *Contraseñas de aplicaciones*). Te dará 16 letras. Es distinta de tu contraseña normal y se puede borrar cuando quieras.
2. En tu repositorio: **Settings → Secrets and variables → Actions → New repository secret**. Crea estos seis, uno por uno:

| Nombre | Valor |
|---|---|
| `SUPABASE_URL` | tu Project URL |
| `SUPABASE_ANON_KEY` | tu Publishable key |
| `SUPABASE_SERVICE_KEY` | tu Secret key |
| `SMTP_USER` | tu dirección de Gmail |
| `SMTP_PASSWORD` | las 16 letras de la contraseña de aplicación |
| `EMAIL_TO` | el correo donde quieres recibir el PDF |

3. **Prueba ahora mismo**: pestaña **Actions → Informe mensual → Run workflow**. Deja el mes vacío (usa el mes anterior) o escribe uno como `2026-10`. En un minuto tendrás el email con el PDF.

A partir de ahí llega solo el **día 1 de cada mes** (sobre las 8–9 h en España; GitHub puede retrasarlo unos minutos).

---

## Uso diario

- **Nueva**: partido, importe y cuota. La fecha se pone sola.
- **Resumen**: marca el resultado de las pendientes con un toque (Ganada, Perdida, Nula).
- **Historial**: edita o borra apuestas.
- **Ajustes**: bank inicial (viene en 1000 €), **Exportar CSV** e **Importar CSV**.

## Copia de seguridad

De vez en cuando pulsa **Exportar CSV** en Ajustes. Es tu copia: se abre en Excel o Google Sheets y se puede volver a importar.

## Qué vigilar (gratis tiene letra pequeña)

- **Supabase pausa los proyectos gratuitos con poca actividad durante 7 días.** La tarea «Mantener activa» hace una petición cada lunes para evitarlo. Si alguna vez la web dice «Sin conexión» durante días, entra en supabase.com, abre el proyecto y pulsa **Resume project**: los datos se conservan (la documentación de Supabase indica hasta 1 año).
- **GitHub desactiva las tareas programadas tras 60 días sin actividad en el repositorio.** El informe mensual deja un pequeño registro cada mes (un archivo con la fecha, sin datos tuyos) justo para evitarlo. Si un día GitHub te avisa de que las ha desactivado: **Actions → tarea → Enable workflow**.
- **El registro de Actions es público** (el repositorio lo es). Por eso el informe nunca escribe importes ni partidos ahí, solo mensajes de estado.
- **Tu secret key y tu contraseña de aplicación** solo deben estar en los *Secrets* de GitHub. Si crees que se han filtrado, genera otras nuevas.

## Si algo falla

| Síntoma | Qué hacer |
|---|---|
| La web dice «Modo local» | `docs/config.js` está vacío o mal copiado (paso 2). |
| «Correo o contraseña incorrectos» | Usa el usuario del paso 1.4. Puedes cambiar la contraseña en Supabase → Authentication → Users. |
| No llega el email | Mira **Actions → Informe mensual** y abre la ejecución: el error dice qué falta. Lo más habitual es la contraseña de aplicación o un secret mal escrito. |
| «Error al leer la base de datos (HTTP 401)» | `SUPABASE_SERVICE_KEY` está mal copiada. |
| Quiero otro bank inicial | Resumen → Ajustes → Bank inicial. |

## Cómo está construido (por si quieres tocar algo)

- La web es un único `docs/index.html` sin dependencias; habla con Supabase por su API. Si `config.js` está vacío, guarda los datos en el navegador (modo local).
- Cada fila de la base de datos pertenece a tu usuario y solo tú puedes leerla o cambiarla (reglas RLS en `schema.sql`).
- El informe (`informe/informe_mensual.py`) usa las mismas fórmulas que la web: beneficio, yield, acierto, ventaja sobre las cuotas, profit factor, rachas y caída máxima. Se puede probar a mano:
  `python informe/informe_mensual.py --mes 2026-10 --no-enviar` (necesita `SUPABASE_URL` y `SUPABASE_SERVICE_KEY`).
