# laundry-ops-api

## Pruebas

La suite usa pytest, pytest-django y APIClient de DRF. PostgreSQL debe estar disponible con la configuración de `.env`; pytest-django crea una base de pruebas separada. Los tests generan sus propios usuarios, clientes y catálogo, sin requerir `runserver`.

```powershell
. .\.venv\Scripts\Activate.ps1
python -m pytest
```

Las pruebas de autenticación y el flujo completo de órdenes obtienen JWT mediante login y envían el access en el encabezado Bearer. Las pruebas específicas de negocio usan `force_authenticate()` para omitir únicamente la validación del token; los serializers, servicios y escrituras en PostgreSQL se ejecutan normalmente.

La cobertura es selectiva: autenticación, validaciones de IDs y filtros, cálculos y precios históricos, estados y rollback de operaciones rechazadas. La expiración del access se prueba con un token vencido, sin esperar su hora de duración. Logout revoca el refresh, pero un access ya emitido sigue siendo válido hasta su expiración.

## Django Admin

Los usuarios se desactivan con `is_active=False`; no se pueden eliminar desde Admin. El correo es opcional y se guarda como `NULL` cuando está vacío.

Las órdenes se crean y sus ítems se modifican por la API, que valida y calcula los importes. Admin muestra número, cantidades, precios, subtotales y total como solo lectura; no permite agregar ni quitar ítems. Se mantienen las correcciones de notas, estados y seguimiento de lavado al seco.

## Gastos operativos

El módulo `expenses` implementa el registro plano del ADR-020, separado de órdenes y pagos. La API exige JWT de un usuario activo con `role=ADMIN`; las banderas `is_staff` e `is_superuser` no sustituyen el rol de negocio. Los operadores reciben `403`, incluso si son superusuarios. Sin JWT válido devuelve `401`.

| Método y ruta | Operación |
|---|---|
| `POST /api/expenses/` | Registrar un gasto; devuelve `201`. |
| `GET /api/expenses/` | Listar gastos; devuelve `200` con `count`, `next`, `previous` y `results`. Paginación de 20, orden por fecha de gasto descendente y luego ID descendente. |
| `GET /api/expenses/{id}/` | Consultar un gasto; devuelve `200` o `404` si no existe. |

Ejemplo de registro:

```json
{"amount": "25.40", "concept": "Detergente", "category": "SUPPLIES", "date": "2026-10-02", "receipt_number": "B001-123"}
```

`amount`, `concept`, `category` y `date` son obligatorios. El importe debe ser mayor que cero, tener como máximo dos decimales y no superar `99999999.99`; se calcula con `Decimal` y se devuelve como cadena con dos decimales. PostgreSQL también exige un importe positivo. Los datos inválidos devuelven `400`, sin crear registros.

El concepto admite hasta 255 caracteres y no puede quedar vacío después de recortar espacios externos. Las categorías son `SUPPLIES` (insumos), `SERVICES` (servicios), `OUTSOURCING` (tercerización), `MAINTENANCE` (mantenimiento) y `OTHER` (otros). `date` es la fecha en que ocurrió el gasto, no una fecha de registro asignada por el servidor; se envía como `YYYY-MM-DD` y no tiene restricciones adicionales sobre fechas pasadas o futuras.

`receipt_number` es opcional y admite hasta 100 caracteres. Ausente, `null`, vacío o compuesto solo por espacios se guarda y devuelve como `null`; los demás valores se recortan. No es un identificador único. El servidor asigna `id` y `created_by`; los valores enviados para esos campos no se usan. El responsable se devuelve como un objeto con `id` y `name` y está protegido contra eliminación.

Ejemplo de respuesta:

```json
{"id": 1, "amount": "25.40", "concept": "Detergente", "category": "SUPPLIES", "date": "2026-10-02", "receipt_number": "B001-123", "created_by": {"id": 1, "name": "Administrador"}}
```

### Correcciones y límites

`PUT`, `PATCH` y `DELETE` no están permitidos en la API (`405` para un administrador autenticado). Las correcciones se realizan en Django Admin → Expenses, con `role=ADMIN`, acceso de personal activo a Admin y el permiso nativo `expenses.change_expense`; `expenses.view_expense` solo permite consultar. El responsable original es de solo lectura. Admin tampoco permite altas ni eliminación individual o masiva: el registro inicial se hace por la API.

El historial nativo de Admin registra el usuario, la fecha y los campos modificados; no conserva versiones completas de los valores anteriores ni implementa anulaciones. El módulo no tiene aprobaciones, reembolsos, caja, cálculos de ganancias ni integración con el dashboard. Tampoco añade idempotencia: repetir un `POST` exitoso crea otro gasto, aunque se envíe el mismo comprobante. Esta versión no incluye filtros de listado por API; Admin permite buscar por concepto o comprobante y filtrar por fecha o categoría.

### Migración y pruebas

`expenses.0001_initial` crea únicamente la tabla del módulo, su relación protegida con usuarios y la restricción de importe positivo; no altera las tablas de órdenes o pagos ni regenera migraciones existentes. Para habilitar el módulo en un entorno, aplicar las migraciones habituales:

```powershell
. .\.venv\Scripts\Activate.ps1
python manage.py migrate
```

Las regresiones reutilizan pytest existente en una base PostgreSQL aislada: login y JWT real, roles, importes, campos obligatorios, comprobantes, autoría no falsificable, paginación sin consultas por cada resultado, correcciones con historial y permisos de Admin, bloqueo de eliminación y restricciones de base de datos. No se configura CI ni una nueva infraestructura de pruebas.

## Pagos

Registro manual para administradores y operadores autenticados mediante `Authorization: Bearer <access>`. No integra pasarelas, bancos ni Yape/Plin: el operador registra un cobro realizado fuera del sistema. No incluye devoluciones, caja formal ni frontend.

### Contratos

| Método y ruta | Operación |
|---|---|
| `POST /api/orders/` | Crear una orden con `payment` opcional; la orden y el adelanto positivo se guardan en una única transacción. |
| `POST /api/orders/{order_id}/payments/` | Registrar un cobro posterior. |
| `GET /api/orders/{order_id}/payments/` | Historial completo, incluidos pagos anulados, ordenado por fecha e identificador; sin paginación. |

Adelanto al crear una orden:

```json
{"client": 1, "items": [{"catalog_item": 1, "quantity": "2.00"}], "notes": "", "payment": {"amount": "5.00", "payment_method": "CASH", "reference_code": ""}}
```

Cobro posterior:

```json
{"amount": "5.00", "payment_method": "YAPE_PLIN", "reference_code": "OP-123"}
```

En la creación, `payment` ausente, `null` o con `amount` cero significa sin adelanto y no genera un pago. Para un importe positivo se exige `CASH` o `YAPE_PLIN`; `reference_code` es opcional y admite hasta 100 caracteres. Los cobros posteriores deben ser mayores que cero, tener como máximo dos decimales y no superar el saldo vigente. Se recomienda enviar los importes como cadenas decimales.

Cada pago devuelve `id`, `order` (ID), `amount`, `payment_method`, `reference_code`, `payment_type`, `created_at`, `created_by`, `voided_at`, `voided_by` y `void_reason`. Los responsables son objetos con `id` y `name`; `voided_by` y `voided_at` son `null` mientras el pago esté vigente. La orden, el tipo, las fechas y los responsables los asigna el servidor, no la petición. Los importes se devuelven como cadenas con dos decimales.

| Código | Significado |
|---|---|
| `201` | Orden o pago registrado. |
| `200` | Historial consultado o reintento reconocido sin duplicar recursos. |
| `400` | Datos, importe o encabezado de idempotencia inválidos. |
| `401` | JWT ausente, inválido o vencido. |
| `404` | Orden inexistente. |
| `409` | Estado incompatible, edición económica bloqueada o clave reutilizada para otra petición. |

No existen endpoints para editar, eliminar ni anular pagos; `PUT`, `PATCH` y `DELETE` en la colección no están permitidos.

### Saldos y estados

- `paid_amount` es la suma SQL de pagos no anulados; `balance = total_amount - paid_amount`. No se almacenan contadores adicionales de saldo.
- El tipo se calcula automáticamente: `ADVANCE` para un adelanto parcial, `PARTIAL` para un cobro posterior que deja saldo y `FINAL` para cualquier pago que liquide la deuda.
- Se puede entregar una orden con deuda y cobrar después de la entrega. Cancelar conserva los pagos vigentes como ingresos y rechaza nuevos cobros; no produce una devolución ni anulación automática.
- Mientras exista algún pago vigente, enviar `items` para editar la orden devuelve `409`. Cliente y notas siguen siendo editables en órdenes activas. Al anular todos los pagos, la edición económica vuelve a permitirse si la orden sigue activa; las entregadas y canceladas continúan cerradas para edición por API.
- Cobros, anulaciones y comprobación del bloqueo económico se realizan en transacciones que bloquean la misma orden. Admin guarda solamente campos editables de órdenes e ítems, sin sobrescribir importes con datos antiguos.

### Reintentos seguros

Para todo cobro posterior y para crear una orden con adelanto positivo, enviar `Idempotency-Key` con un UUID, por ejemplo `14b48b20-78e8-4ff8-8eba-4b03b40e3e93`. Generar una nueva clave por operación y conservarla para reintentar esa misma petición. El encabezado está habilitado en CORS para el frontend separado.

La clave es única entre todos los pagos. Se guarda una huella SHA-256 del usuario, método HTTP, ruta y cuerpo JSON canónico: el orden de las propiedades y los espacios no afectan la huella, pero cambiar valores, usuario u operación sí. Un reintento idéntico devuelve `200` y el recurso existente; para la creación con adelanto devuelve la orden original. La representación refleja su estado actual. Una reutilización incompatible devuelve `409`, incluso entre órdenes distintas. La restricción única de PostgreSQL resuelve también peticiones simultáneas; la transacción duplicada se revierte antes de recuperar el resultado original.

Reintentar un pago anulado devuelve ese registro anulado y nunca lo reactiva. Las peticiones fallidas no reservan claves. Las órdenes creadas sin adelanto quedan fuera de esta protección: repetirlas puede crear otra orden aunque se envíe el mismo encabezado.

### Anulación administrativa

En Django Admin → Payments, seleccionar exactamente un pago y ejecutar «Anular un pago seleccionado». La pantalla de confirmación exige un motivo de hasta 255 caracteres. La acción requiere el permiso nativo `payments.change_payment`; ese permiso no habilita edición genérica del pago. El rol personalizado por sí solo no concede acceso a Admin.

Admin no permite altas, edición ni eliminación de pagos. La anulación conserva los datos originales, registra fecha, responsable y motivo, y genera una entrada en el historial de Admin. No puede repetirse ni revertirse. Es una corrección del registro, no una devolución de dinero. Los pagos, incluso anulados, impiden eliminar su orden; sus responsables también están protegidos.

### Migración y verificación

La migración incremental `payments.0002_payment_integrity_and_voiding` conserva los registros existentes como vigentes y deja sus claves de idempotencia en `NULL`. Elimina la opción `OTHER`, añade la auditoría de anulación, protege el historial y exige `amount > 0` en PostgreSQL.

Antes de aplicarla en otro entorno, comprobar los pagos existentes:

```sql
SELECT id, amount, payment_method FROM payments_payment WHERE amount <= 0 OR payment_method NOT IN ('CASH', 'YAPE_PLIN');
```

Si aparecen filas, detener el despliegue y revisarlas manualmente. La propia migración aborta ante esos datos; no los convierte automáticamente. No se regeneran migraciones iniciales ni se reinicia la base.

```powershell
. .\.venv\Scripts\Activate.ps1
python manage.py migrate
```

Las pruebas incluyen rollback de adelantos, JWT real para ambos roles, saldos, bloqueo económico, permisos y trazabilidad en Admin, restricciones de PostgreSQL, migración de registros antiguos y carreras con transacciones reales. Los tests concurrentes comprueban que nunca se sobrepague ni se dupliquen pagos u órdenes. Toda la suite usa una base aislada creada por pytest-django; no altera datos de desarrollo.

```powershell
. .\.venv\Scripts\Activate.ps1
python -m pytest -p no:cacheprovider
python -m ruff check . --no-cache
python manage.py check
python manage.py makemigrations --check --dry-run
```
