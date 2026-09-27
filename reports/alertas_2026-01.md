# Alertas del periodo 2026-01

**Dos señales de churn de origen distinto: paid_search dispara el churn voluntario (9,74% vs 4,35% esperado, 15 bajas) con caída simultánea de su conversión trial→pago, y contador_aliado muestra churn involuntario al 4,9% acompañado de más fallos de cobro (7,95%).**

## [ALTA] Churn voluntario en paid_search se duplica y su conversión trial→pago se hunde

El churn voluntario de clientes de paid_search llegó a 9,74% en enero (15 bajas sobre 154 clientes activos al inicio) frente a un baseline de 4,35% de los 6 meses previos, ~8,3 bajas por encima de lo esperado (z=3,28). La serie venía bajando (5,45% en jul-ago, 3,10% en nov, 2,84% en dic), así que es un quiebre brusco, no una tendencia. Es la alerta con mayor impacto del mes por volumen de logos perdidos y porque el churn voluntario apunta a producto, precio o encaje del cliente, no a operaciones de cobro.

*Señales relacionadas:* En el mismo canal, la conversión trial→pago cayó a 11,67% en enero desde 36,07% en diciembre y 33,33% en noviembre (no generó alerta propia, probablemente por tamaño de cohorte). El churn involuntario de paid_search está en mínimos (0,65%) y su tasa de fallo de pagos es normal (4,61%), lo que descarta un problema de cobro.

**Hipótesis**
1. Deterioro de la calidad de la adquisición pagada: cohortes recientes con peor encaje entran y salen rápido, lo que explicaría a la vez más churn voluntario y menos conversión de trial en el mismo canal.
2. Cambio de precio, empaquetado o condiciones que afecta sobre todo a clientes autoservicio llegados por búsqueda pagada, más sensibles al precio que los referidos por contadores.
3. Concentración de bajas en una cohorte o plan concreto (p. ej. cierre de año fiscal con clientes que solo usaban el producto por temporada) que infla un mes aislado.

**Para confirmar:** Abrir las 15 bajas voluntarias de paid_search por mes de alta, plan y país, y compararlas con el motivo de cancelación registrado; en paralelo, mirar la antigüedad media de esas bajas: si son mayoritariamente altas de los últimos 2-3 meses, confirma el problema de calidad de adquisición.

## [MEDIA] Churn involuntario en contador_aliado sube junto con los fallos de cobro

El churn involuntario de clientes del canal contador_aliado subió a 4,9% (7 bajas sobre 143 clientes) frente a un baseline de 1,16%, ~5,3 bajas por encima de lo esperado (z=3,50). En el mismo mes la tasa de fallo de pagos del canal pasó a 7,95% desde 4,86% en diciembre y un rango de 3,8%-6,0% en los seis meses previos, el valor más alto de la serie. El churn voluntario del canal, en cambio, bajó a 4,20% (vs 6,57% en diciembre), así que el deterioro es puramente de cobranza.

*Señales relacionadas:* El alza de payment_failure_rate del canal (7,95%) no cruzó el umbral por sí sola pero es la explicación directa de la alerta; la conversión trial→pago del canal mejoró a 63,33%, sin relación con este problema.

**Hipótesis**
1. Problema de cobro concentrado en este canal: rechazos de una pasarela o de un emisor local durante enero que no se recuperaron con los reintentos, convirtiendo fallos en cancelaciones.
2. Tarjetas vencidas o renovadas a comienzo de año sin actualizar, con recordatorios de actualización que no llegaron o llegaron tarde.
3. Volatilidad de base pequeña: 7 bajas sobre 143 clientes en una serie que tuvo 0% en octubre y noviembre; un par de casos de más mueve mucho la tasa.

**Para confirmar:** Cruzar los 7 churns involuntarios con el log de intentos de cobro: código de rechazo, banco emisor, país y pasarela, y ver cuántos tuvieron reintento completo antes del corte. Si los rechazos se concentran en un emisor o pasarela, es operativo y recuperable.

## Probable ruido

Ambas alertas tienen z apenas sobre el umbral (3,50 y 3,28) y excesos pequeños (5,3 y 8,3 eventos) en un mes en que se evaluaron 58 series, así que una sola podría deberse al azar. La de paid_search (A2) gana credibilidad porque otra métrica del mismo canal se mueve en la misma dirección (conversión trial→pago 11,67% vs 36,07% en diciembre); la de contador_aliado (A1) se apoya en el alza simultánea de fallos de pago, pero su serie de churn involuntario es muy inestable (0% en octubre y noviembre, 2,59% en septiembre) y conviene confirmarla con febrero antes de tratarla como cambio de nivel. La mejora del churn voluntario en contador_aliado (4,20% vs 6,57%) y la caída del churn involuntario en paid_search (0,65%) no son relevantes por sí solas.
