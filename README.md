# Consultorios Compartidos

Sistema de Astrogato Labs para administrar el uso compartido de consultorios médicos.
Esta primera iteración construye la base arquitectónica modular, sin implementar todavía
reservaciones reales, cálculo tarifario, pagos, OCR, sellado digital ni control de accesos.

## Stack

- Python 3.14
- Django 5.2 LTS
- PostgreSQL
- Redis
- Celery y Celery Beat
- HTMX
- Bootstrap 5
- MinIO preparado para documentos
- Podman Compose compatible con Docker Compose
- uv
- pytest, ruff, black, mypy y pre-commit
- django-environ

## Estructura modular

```text
config/                 Configuración Django, URLs, WSGI, ASGI y Celery
apps/core/              Infraestructura compartida
apps/identity/          Usuarios, roles y autenticación por email
apps/catalog/           Catálogos de clínicas, consultorios y perfiles
apps/scheduling/        Agenda, disponibilidad y reservaciones
apps/finance/           Tarifas, estados de cuenta y pagos
apps/vault/             Documentos, hashes y storage preparado
apps/astrotrace/        Trazabilidad, eventos y evidencias
apps/integration/       Integraciones externas
apps/presentation/      Capa web, navegación y vistas base
templates/              Layout, parciales y páginas iniciales
static/                 Assets estáticos del proyecto
tests/                  Pruebas mínimas de arranque
```

## Variables de entorno

Copiar `.env.example` a `.env` y cambiar los valores `change-me-*`.
Las credenciales no se guardan en código. En desarrollo local, si no se define
`DATABASE_URL`, Django usa SQLite para facilitar pruebas rápidas. En contenedores,
`podman-compose.yml` fuerza PostgreSQL.

Variables principales:

- `DJANGO_DEBUG`
- `DJANGO_SECRET_KEY`
- `DJANGO_ALLOWED_HOSTS`
- `DJANGO_CSRF_TRUSTED_ORIGINS`
- `DATABASE_URL`
- `REDIS_URL`
- `CELERY_BROKER_URL`
- `CELERY_RESULT_BACKEND`
- `MINIO_STORAGE_ENABLED`
- `AWS_ACCESS_KEY_ID`
- `AWS_SECRET_ACCESS_KEY`
- `AWS_STORAGE_BUCKET_NAME`
- `AWS_S3_ENDPOINT_URL`

## Instalación local con uv

```bash
uv sync --group dev
uv run python manage.py migrate
uv run python manage.py createsuperuser
uv run python manage.py runserver
```

La aplicación queda disponible en `http://127.0.0.1:8000/`.

## Ejecución con Podman Compose

```bash
cp .env.example .env
podman compose -f podman-compose.yml up --build
```

También puede ejecutarse con Docker Compose:

```bash
docker compose -f podman-compose.yml up --build
```

## Pruebas

```bash
uv run pytest
```

Las pruebas mínimas validan que Django arranca, `CustomUser` funciona, las vistas
base responden y las migraciones están sincronizadas con los modelos.

## Calidad

```bash
uv run ruff check .
uv run black --check .
uv run mypy .
```

Instalar hooks locales:

```bash
uv run pre-commit install
```

## Bóveda Documental

Los documentos se almacenan con el storage configurado de Django. En desarrollo,
si `MINIO_STORAGE_ENABLED=false`, se usa `FileSystemStorage` local bajo
`MEDIA_ROOT` y rutas internas `vault/YYYY/MM/`. La visualización/descarga pasa por
una vista autenticada; no se enlaza directamente la ruta física del archivo.
MinIO queda disponible mediante las variables `MINIO_STORAGE_ENABLED` y `AWS_*`.

## Datos iniciales

El superadministrador semilla se crea desde variables de entorno:

```bash
ADMIN_EMAIL=admin@example.com \
ADMIN_PASSWORD=change-me \
ADMIN_FIRST_NAME=Admin \
ADMIN_LAST_NAME=Principal \
uv run python manage.py seed_initial_data
```

No hay credenciales hardcodeadas en el comando.

## Catálogos Operativos

Rutas principales del MVP:

- `/clinicas/`
- `/consultorios/`
- `/especialidades/`
- `/equipamiento/`
- `/propietarios/`
- `/medicos-arrendatarios/`

Cada catálogo cuenta con listado, alta, edición, detalle y desactivación lógica.

## Preparación para pruebas de usuario

1. Instalar dependencias y aplicar migraciones:

```bash
uv sync --group dev
uv run python manage.py migrate
```

2. Crear el superadministrador semilla con variables de entorno:

```bash
ADMIN_EMAIL=admin@example.com \
ADMIN_PASSWORD=change-me \
ADMIN_FIRST_NAME=Admin \
ADMIN_LAST_NAME=Principal \
uv run python manage.py seed_initial_data
```

3. Cargar un escenario demo idempotente:

```bash
uv run python manage.py seed_demo_data
```

El comando crea 2 clínicas, 4 consultorios, 3 especialidades, 2 propietarios,
4 médicos arrendatarios, disponibilidad, tarifas, reservaciones en distintos
estados, pagos, liquidaciones, documentos, eventos AstroTrace y accesos simulados.
También crea usuarios representativos por rol. La contraseña demo por defecto es
`DemoPass123!` y puede cambiarse temporalmente con `DEMO_PASSWORD`.

4. Levantar la aplicación:

```bash
uv run python manage.py runserver
```

También puedes usar el panel TUI local:

```bash
control_tui.bat
```

El panel usa Textual y permite preparar dependencias, aplicar migraciones,
verificar el proyecto, levantar Django en `http://127.0.0.1:8000/`, abrir el
navegador y apagar el proceso iniciado por el propio TUI.

5. Smoke test manual sugerido:

- Entrar a `/`, `/dashboard/`, `/clinicas/`, `/consultorios/`,
  `/disponibilidad/`, `/calendario/`, `/tarifas/`, `/reservaciones/`,
  `/pagos/`, `/liquidaciones/`, `/documentos/`, `/timeline/`,
  `/integraciones/accesos/` y `/reportes/`.
- Revisar búsqueda, paginación, badges de estado, breadcrumbs y enlaces rápidos.
- Validar que Auditor no pueda ejecutar acciones de escritura y que Recepción
  no tenga acceso a finanzas completas.
# ConsultoriosCompartidos
