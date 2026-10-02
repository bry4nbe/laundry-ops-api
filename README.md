# laundry-ops-api

## Pruebas

La suite usa pytest, pytest-django y APIClient de DRF. PostgreSQL debe estar disponible con la configuración de `.env`; pytest-django crea una base de pruebas separada. Los tests generan sus propios usuarios, clientes y catálogo, sin requerir `runserver`.

```powershell
. .\.venv\Scripts\Activate.ps1
python -m pytest
```

Las pruebas de autenticación y el flujo completo de órdenes obtienen JWT mediante login y envían el access en el encabezado Bearer. Las pruebas específicas de negocio usan `force_authenticate()` para omitir únicamente la validación del token; los serializers, servicios y escrituras en PostgreSQL se ejecutan normalmente.

La cobertura es selectiva: autenticación, validaciones de IDs y filtros, cálculos y precios históricos, estados y rollback de operaciones rechazadas. La expiración del access se prueba con un token vencido, sin esperar su hora de duración. Logout revoca el refresh, pero un access ya emitido sigue siendo válido hasta su expiración.

## Dashboard operativo y financiero

`GET /api/dashboard/` devuelve un resumen sin paginación y exige JWT de un usuario con `role=ADMIN`. Los operadores reciben `403`, aunque tengan `is_staff` o `is_superuser`; esas banderas no sustituyen el rol de negocio. Sin JWT válido devuelve `401` y los métodos de escritura devuelven `405` para un administrador autenticado.

Sin filtros consulta hoy según `America/Lima`. Para consultar un período, enviar `date_from` y `date_to` juntos, en formato `YYYY-MM-DD`, por ejemplo `/api/dashboard/?date_from=2026-10-01&date_to=2026-10-02`. Ambos días están incluidos. Las fechas inválidas, incompletas, invertidas o cuyo límite final no sea representable devuelven `400`. Semana y mes se expresan con fechas, sin filtros especiales.

Ejemplo de respuesta:

```json
{"date_from": "2026-10-02", "date_to": "2026-10-02", "orders_created": 3, "pending_delivery_count": 2, "collected_amount": "31.00", "collected_by_method": {"CASH": "18.00", "YAPE_PLIN": "13.00"}, "outstanding_amount": "22.00", "current_month_monitor": {"month": "2026-10", "collected_amount": "31.00", "above_5000": false, "above_8000": false, "indicative_only": true}}
```

- `orders_created` cuenta todas las órdenes ingresadas en el rango, incluidas las posteriormente canceladas.
- `pending_delivery_count` es global y actual: cuenta órdenes sin `delivered_at` ni `cancelled_at`, independientemente del rango.
- `collected_amount` y `collected_by_method` suman pagos vigentes por fecha de cobro, no por fecha de creación de la orden. Los pagos vigentes de órdenes canceladas siguen siendo ingresos. Siempre se devuelven `CASH` y `YAPE_PLIN`.
- `outstanding_amount` suma el saldo actual de órdenes creadas en el rango: incluye entregadas con deuda y excluye canceladas. Resta todos sus pagos vigentes, incluso los registrados fuera del rango. No reconstruye la deuda histórica al cierre del período.
- Los importes son cadenas con dos decimales y pueden superar el máximo de una orden individual. Los conteos son enteros; sin datos se devuelven ceros, no `null`.
- Anular un pago recalcula también los cobros de períodos anteriores y el saldo vigente. No representa una devolución. Los resultados se calculan mediante agregación SQL, sin caché, y se actualizan en cada petición; el refresco automático de pantalla corresponde al futuro frontend.

### Monitor mensual orientativo

`current_month_monitor` corresponde siempre al mes calendario actual en Lima, no al período filtrado. Sus cobros excluyen anulados. `above_5000` y `above_8000` se activan estrictamente por encima de S/ 5,000 y S/ 8,000; igualar un umbral no lo supera. `indicative_only` siempre es `true`.

Este monitor solo compara cobros registrados con referencias mensuales: no determina la categoría NRUS, la obligación de pago ni el cumplimiento tributario. SUNAT considera también compras y otros límites que esta versión no calcula. [Referencia oficial de SUNAT](https://www.gob.pe/institucion/sunat/pages/6988-nuevo-regimen-unico-simplificado-nrus).

### Buscar pendientes de entrega

Administradores y operadores usan el listado existente: `GET /api/orders/?delivered=false&search=Marco`. `search` busca coincidencias parciales por nombre del cliente o número de orden, sin distinguir mayúsculas y recortando espacios externos. Una búsqueda vacía no filtra. Puede combinarse con los filtros existentes de cliente y fechas; `delivered=false` excluye entregadas y canceladas.

Se conserva la respuesta de órdenes con sus saldos reales, paginación de 20 y orden de más reciente a más antigua, desempate por identificador. No existe otro endpoint de pendientes.

El dashboard no introduce modelos ni migraciones. US-19 queda parcialmente cubierta: costos del tercero, ganancias y distribución financiera por servicio están diferidos. No incluye frontend, gastos, caja formal ni reportes fiscales, y no configura CI ni pytest. Las regresiones reutilizan la suite existente sobre una base aislada.

## Django Admin

Los usuarios se desactivan con `is_active=False`; no se pueden eliminar desde Admin. El correo es opcional y se guarda como `NULL` cuando está vacío.

Las órdenes se crean y sus ítems se modifican por la API, que valida y calcula los importes. Admin muestra número, cantidades, precios, subtotales y total como solo lectura; no permite agregar ni quitar ítems. Se mantienen las correcciones de notas, estados y seguimiento de lavado al seco.

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
