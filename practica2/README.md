# Práctica 2: solución de referencia

## Alcance

Proyecto completo del laboratorio: PostgreSQL 16 con MASTER y dos suscriptores lógicos filtrados, API HTTP, balanceo de lecturas elegibles, monitor, outbox SQLite, RabbitMQ, auditoría idempotente y pgAdmin. No es producción ni replicación multimaestro. Requiere Docker Compose v2 reciente, acceso a registros de imágenes y Python 3 local para las pruebas HTTP/Docker.

Todos los archivos de la solución se incluyen en este ZIP; respete la estructura de carpetas.

## Arquitectura

- Escrituras: Nginx -> API -> MASTER. No hay escritura directa en las réplicas con app_user.
- Lecturas regionales eventuales: alternan 50/50 MASTER filtrado y réplica READY. Lecturas globales/strong: MASTER.
- MASTER publica Norte y Sur por separado; suscriptores conectan al publicador. La clave (region,id) incluye la región requerida por el filtro y la identidad de réplica.
- Monitor independiente: registra transición + outbox en SQLite durable, y verifica datos regionales antes del reingreso.
- Un solo monitor, publicador y consumidor en este laboratorio. SQLite compartido es una simplificación para un único host Docker, no un almacén distribuido ni montaje NFS.
- RabbitMQ entrega eventos; NO transporta filas de replicación PostgreSQL.

## Arranque

Desde la carpeta donde está docker-compose.yml:

```bash
docker compose config
docker compose up -d --build
docker compose ps -a
docker compose logs bootstrap
docker compose logs -f monitor eventos
```

bootstrap debe terminar con Exit 0; no permanece ejecutándose. La API espera su finalización. READY puede tardar por copia inicial y dos verificaciones; running o healthy no significa replicación sincronizada. api/health comprueba proceso HTTP, no convergencia ni todos sus componentes.

Puertos: API 8080, pgAdmin 5050, RabbitMQ 15672, MASTER 5433, NORTE 5434, SUR 5435. Todos publicados en 127.0.0.1. Internamente PostgreSQL usa 5432.

Usuarios de laboratorio:

| Uso | Usuario | Clave |
|---|---|---|
| PostgreSQL administrador | labuser | labpass |
| API / consulta regional | app_user | apppass |
| Monitor | monitor_user | monitorpass |
| Replicación (solo MASTER) | replicador | replpass |
| RabbitMQ | labuser | labpass |
| pgAdmin web | docente@practica.com | adminpass |

Credenciales deliberadamente visibles para laboratorio local. No usar en producción. pgAdmin precarga los tres servidores; al conectarse introducir labpass.

## Inicialización y replicación

postgres/common.sql crea tabla y roles en los tres nodos. postgres/master.sql añade permisos de alta, tabla de idempotencia, replicador y publicaciones. Los scripts de init solo se ejecutan con volumen vacío. El acceso host SCRAM que inicializa la imagen permite las conexiones lógicas a labdb; para producción restringir pg_hba.conf a roles, bases y subred explícitos.

bootstrap crea suscripciones en autocommit; no vuelve a crearlas si existen. Si se elimina únicamente el suscriptor pero se conserva su slot en MASTER, el bootstrap no repara automáticamente ese estado: diagnosticar slots y reconstruir de forma controlada. No borrar slots para resolver una simple desconexión.

```bash
docker compose exec pg_master psql -U labuser -d labdb -c 'TABLE pg_publication;'
docker compose exec pg_nodo_norte psql -U labuser -d labdb -c 'SELECT subname,pid,received_lsn FROM pg_stat_subscription;'
docker compose exec pg_nodo_sur psql -U labuser -d labdb -c 'SELECT srrelid::regclass,srsubstate FROM pg_subscription_rel;'
docker compose exec pg_master psql -U labuser -d labdb -c 'SELECT slot_name,active,wal_status FROM pg_replication_slots;'
```

## API terminada

```text
POST /api/clientes/master
POST /api/clientes/norte
POST /api/clientes/sur
GET  /api/clientes?region=Norte
GET  /api/clientes?region=Sur
GET  /api/clientes?region=Norte&consistency=strong
GET  /api/clientes?consistency=strong
GET  /api/nodos
GET  /api/eventos
GET  /health
```

Ejemplo Bash:

```bash
curl -i -X POST http://localhost:8080/api/clientes/norte \
 -H 'Content-Type: application/json' \
 -d '{"id":901,"region":"Norte","nombre":"Ejemplo","saldo":"150.00","operacion_id":"078b33a7-efac-4ac8-aa42-2d213ffb5867"}'
curl 'http://localhost:8080/api/clientes?region=Norte'
curl http://localhost:8080/api/nodos
```

Ejecutar este ejemplo en entorno separado o después de las pruebas de conteos, porque añade una fila. Cada canal rellena origen_ingreso; el destino de escritura es siempre MASTER. Responde 201, repetición idéntica 200 y operación cambiada 409. Ante 503 durante una escritura, el cliente no puede asumir rechazo: reintentar el mismo operacion_id para consultar/repetir idempotentemente.

La API valida saldo, región, UUID y tipo de ID. No ofrece UPDATE/DELETE HTTP ni cancelación. La replicación soporta esas operaciones SQL administrativas para región inmutable, pero son ampliaciones fuera de las pruebas. No usar TRUNCATE: no se publica.

## Pruebas automatizadas: orden obligatorio

Ejecutar desde la raíz del proyecto en un entorno de clientes inicialmente vacío. El script depende solo de biblioteca estándar y docker CLI local. Detiene/reconecta componentes; no ejecutar pruebas concurrentes.

```bash
python pruebas/laboratorio.py inicial
python pruebas/laboratorio.py norte
python pruebas/laboratorio.py sur
python pruebas/laboratorio.py rabbitmq
python pruebas/laboratorio.py master
```

Windows: usar py en lugar de python si corresponde. El script usa subprocess sin comandos de shell y funciona con docker CLI accesible. No ejecutar el script local dentro de un contenedor sin Docker CLI.

1. Inicial: 10 altas MASTER (5 por región), 10 NORTE y 10 SUR. Esperado 30/15/15. Verifica operación repetida, conflicto y 20 lecturas por región, 10 en MASTER y 10 en la réplica.
2. Norte: desconecta practica2_datos, espera DOWN, verifica fallback y registra 10 clientes adicionales. Reconecta con alias. Esperado 40/25/15.
3. Sur: repite incidente regional. Esperado 50/25/25.
4. RabbitMQ: con broker detenido fuerza caída y recuperación Norte; los eventos quedan en SQLite, y al iniciar broker se publican. Verifica outbox drenado, auditoría completa y secuencia sin huecos.
5. MASTER: lo detiene, espera DOWN, prueba escritura 503, lecturas regionales eventuales y lectura global 503. No promueve réplicas.

El script inicial tolera volver a enviar operaciones del mismo lote, pero no repara una carga parcial arbitraria. Los incidentes tienen precondiciones de conteo para evitar atribuir evidencia a un escenario incorrecto. No son pruebas de carga ni garantía de tiempos exactos.

## Prueba de sesiones inactivas a 40 segundos

Después de READY y sin otros incidentes, en Bash:

```bash
docker compose exec -T api python < pruebas/conexiones.py
```

Alternativa portable (también PowerShell):

```bash
docker compose cp pruebas/conexiones.py api:/tmp/conexiones.py
docker compose exec -T api python /tmp/conexiones.py
```

Comprueba dos sesiones cerradas tras 45 segundos (idle e idle in transaction), dos replicaciones streaming y una nueva conexión funcional. Los parámetros se aplican solo a app_user, no al administrador, monitor o replicador.

No hay pool ocioso: la API abre/cierra por operación y limita a 10 conexiones simultáneas por proceso mediante semáforo. El timeout de 40 s actúa como protección del rol ante sesiones que quedan abiertas; no afirmar que se implementa idleTimeoutMillis de un pool. El total de conexiones incluye otros procesos y debe dimensionarse al escalar.

## Inspección SQL y permisos

```bash
docker compose exec pg_master psql -U labuser -d labdb -c 'SELECT region,count(*) FROM clientes GROUP BY region;'
docker compose exec pg_nodo_norte psql -U labuser -d labdb -c 'SELECT region,count(*) FROM clientes GROUP BY region;'
docker compose exec pg_nodo_sur psql -U labuser -d labdb -c 'SELECT region,count(*) FROM clientes GROUP BY region;'
docker compose exec pg_nodo_norte psql -U labuser -d labdb -c "SET ROLE app_user; INSERT INTO clientes VALUES ('Norte',999,'No permitido',0,'NORTE','aaaaaaaa-aaaa-4aaa-8aaa-aaaaaaaaaaaa',now());"
```

El último comando debe fallar por permisos; es una prueba negativa intencional. No cargar manualmente clientes en las réplicas. Por admin se puede ver todo para el ejercicio, lo cual no equivale a aislamiento de seguridad productivo.

## Simulación manual de red (Bash)

```bash
NORTE_ID=$(docker compose ps -q pg_nodo_norte)
docker network disconnect practica2_datos "$NORTE_ID"
# Observar DOWN, lecturas desde MASTER y altas nuevas.
docker network connect --alias pg_nodo_norte practica2_datos "$NORTE_ID"
```

Los scripts Python ya resuelven ID y restauran la red en finally. Una desconexión desde el monitor no permite concluir que el proceso esté apagado; por eso reason es sql_unreachable_from_monitor y no process_crashed.

## Monitor: semántica y límites

Primer fallo SUSPECT con retiro inmediato; tercer fallo DOWN. Primera conexión regional posterior entra RECOVERING. Compara listado completo de columnas de negocio regionales con MASTER y exige dos coincidencias antes de READY. Si un nodo READY deja de coincidir pasa a RECOVERING y solo MASTER sirve su región. Es conservador y no mide únicamente lag temporal.

Para MASTER se usa UP, SUSPECT y DOWN. Si MASTER cae, una réplica anteriormente READY y aún accesible conserva elegibilidad para lecturas explícitamente eventuales, con advertencia de posible desactualización. No obtiene consistencia fuerte ni capacidad de escritura.

Cada ciclo espera tres segundos después del trabajo: la duración real incluye sondas SQL secuenciales y comparación. Las rutas descartan un estado regional con antigüedad superior a 30 s. En redes con múltiples timeouts puede provocar fallback conservador; no es SLA de 3 s.

La comparación de datos es una validación docente para lotes pequeños y escrituras detenidas brevemente. No proporciona snapshot distribuido ni una barrera causal para escrituras continuas. creado_en no participa en la comparación de elegibilidad; sí se replica. Las pruebas comparan datos de negocio e identidades, no solo cantidades.

## Eventos y persistencia

Estados, contador node_sequence, incident_id y outbox se guardan en SQLite /state/state.sqlite con WAL y volumen estado. Una sola instancia de monitor es autoritativa. Su almacenamiento no depende de MASTER; por eso puede registrar su caída.

Exchange topic infra.eventos, cola infra.auditoria, routing db.<nodo>.<estado>. Publisher confirms, mensajes persistentes, cola durable y ACK tras persistir eventos. Duplicados de entrega se identifican por event_id. Prefetch 1 y un publicador conservan secuencia; consumidor valida contador por nodo. No hay orden global causal entre nodos.

Fallos de almacenamiento SQLite se reintentan con requeue; mensajes malformados o secuencia incoherente van a infra.fallidos vía DLX para revisión, sin bucle infinito de mensajes corruptos. No borrar por separado la auditoría y conservar las secuencias de origen sin procedimiento de reconstrucción.

/api/eventos muestra bitácora ordenada por nodo y secuencia. Para ver outbox:

```bash
docker compose exec monitor python -c "import sqlite3; c=sqlite3.connect('/state/state.sqlite'); print(c.execute('select sent,count(*) from outbox group by sent').fetchall())"
```

## Cierre y reconstrucción

```bash
docker compose down
```

Conserva datos. Para borrar TODO el laboratorio, incluyendo auditoría, usuarios de pgAdmin, replicación y clientes:

```bash
docker compose down -v
```

Solo realizar limpieza destructiva con autorización. El límite max_slot_wal_keep_size=1GB evita crecimiento ilimitado del WAL retenido por slots, pero puede invalidar una réplica tras incidentes prolongados. El restablecimiento automático requiere WAL disponible; si se pierde, necesita resincronización controlada. No es alta disponibilidad completa.

## Límites productivos

Servidor Flask de desarrollo, sin autenticación HTTP, TLS, backups, métricas OpenTelemetry ni secretos externos. Los roles usan contraseñas didácticas. Monitor usa permisos pg_monitor para introspección. Volúmenes en un solo host y broker único no resisten pérdida del host. El estado compartido SQLite debe sustituirse por un almacén y coordinación adecuados al distribuir servicios. Fijar imágenes por digest y evaluar actualizaciones antes de producción.

Esta solución ha sido revisada estáticamente al generarse. No se afirma que los contenedores se hayan construido o que las pruebas de integración hayan pasado en ese entorno. Los scripts permiten verificarlo en el equipo del docente.

## Fuentes técnicas

- https://www.postgresql.org/docs/16/logical-replication-row-filter.html
- https://www.postgresql.org/docs/16/sql-createpublication.html
- https://www.postgresql.org/docs/16/runtime-config-client.html
- https://www.rabbitmq.com/docs/confirms
