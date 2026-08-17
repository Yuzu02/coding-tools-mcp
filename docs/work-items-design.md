# Nota de diseño: Work Items y estado por workspace

Esta nota registra las decisiones de diseño para una extensión aditiva del
runtime. No modifica ni reemplaza el contrato MCP v0.3 descrito en
[`SPEC.md`](../SPEC.md) y [`docs/runtime-contract-v0.3.md`](runtime-contract-v0.3.md).

## Decisiones

### Fork aditivo y compatibilidad upstream

La solución será un fork aditivo: añade capacidades y conserva el
comportamiento existente. La compatibilidad con upstream es una restricción
explícita; las extensiones no deben alterar el catálogo, los contratos ni la
semántica de los clientes existentes. Cuando una capacidad nueva requiera una
decisión incompatible, se mantiene fuera de V1.

### Configuración y secretos

- La configuración confiable específica del workspace vive únicamente en el
  workspace y está sujeta a las reglas de confianza de ese workspace.
- Los valores predeterminados globales y los secretos son externos al
  workspace. No se copian al repositorio ni se convierten en estado del
  proyecto.
- La configuración de workspace y los defaults/secrets globales son capas
  distintas; su combinación debe ser explícita y auditable.

### Estado

El estado operativo se almacena en SQLite externo, separado por workspace. No
se crea una base de datos de operación dentro del worktree ni se confunde ese
estado con los archivos versionados del proyecto. La identidad de un workspace
determina el ámbito de aislamiento de su estado.

### Work Items y MCP Tasks

El dominio usará **Work Items** como unidad de trabajo persistente y
reclamable. No se introduce ni se adopta **MCP Tasks** como modelo de dominio:
las tareas MCP, si un transporte las ofrece, son una preocupación de
protocolo y no sustituyen a los Work Items.

### Actor metadata y autoridad del lease

Los metadatos del actor registran quién originó o actuó sobre un Work Item
(por ejemplo, cliente, agente o identidad disponible). Es información de
trazabilidad y no concede autoridad.

La autoridad para reclamar, renovar o liberar un Work Item pertenece al lease
validado por el servicio. El actor metadata nunca sustituye la comprobación de
autoridad ni permite que un cliente se autoasigne un lease válido.

### Ciclo de vida del Work Item

El ciclo de vida es explícito y persistente: creación, disponibilidad para
trabajo, claim exclusivo, ejecución y cierre. Un Work Item puede quedar
bloqueado o cancelado como resultado de la ejecución; cada transición debe
conservar su trazabilidad. La finalización no se infiere de la presencia de
metadatos del actor ni de la desaparición de un proceso cliente.

Los leases tienen duración y deben validarse en cada operación que requiera
autoridad. La expiración o pérdida del lease no borra el Work Item: lo devuelve
a un estado reclamable según la política del ciclo de vida.

### Claims exclusivos

Un Work Item admite como máximo un claim activo a la vez dentro de su
workspace. La exclusividad se garantiza en la capa de persistencia y no por
coordinación optimista entre clientes. Las carreras se resuelven rechazando el
claim perdedor con un resultado explícito y conservando el claim ganador.

### Worktrunk

Worktrunk es la autoridad para la creación, selección, asociación y limpieza
de worktrees. El subsistema de Work Items no inventa una autoridad paralela ni
manipula directamente el ciclo de vida del worktree; referencia el worktree
que Worktrunk haya asignado.

### Límites de versión

V1 se limita al modelo persistente de Work Items, su ciclo de vida, claims
exclusivos, leases, trazabilidad básica y estado SQLite externo por workspace,
manteniendo la compatibilidad upstream.

V2 queda reservado para capacidades posteriores que no son necesarias para el
contrato inicial, como ampliar la coordinación o integrar semánticas de
protocolo adicionales. No se anticipan esas capacidades cambiando V1 de forma
implícita.

No se creará `AGENTS-TASKS.md`. Las instrucciones de proyecto siguen usando
los mecanismos de contexto ya definidos por el runtime y la documentación
canónica existente.
