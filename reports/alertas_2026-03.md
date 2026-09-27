# Alertas del periodo 2026-03

**Un salto de la tasa de fallo de pagos en Colombia (22,7% vs ~4,8% esperado, 41 fallos en exceso) explica prácticamente todo el deterioro de cobros del mes y ya arrastra el churn involuntario de CO a 7,7%; en paralelo, paid_search lleva tres meses con la conversión trial→pago a la mitad.**

## [ALTA] Fallo de cobros en Colombia se dispara y contamina el total

En CO la tasa de fallo de primer intento pasó de 4,4% en febrero (baseline ~4,8%) a 22,71% (52 fallos sobre 229 intentos), 41 fallos por encima de lo esperado. El total del producto subió a 10,28% (80/778) con 40,4 fallos en exceso: es decir, casi todo el exceso global viene de CO. El efecto ya se ve en cobranza: el churn involuntario de clientes en CO subió a 7,73% (17 de 220) frente a un baseline de 1,95%, con ~12,7 bajas involuntarias por encima de lo esperado, mientras el churn voluntario de CO se mantiene en su rango habitual (5,45% vs 3,2-6,0% de los meses previos).

*Señales relacionadas:* A3 (total, 10,28%), A2 (plan pyme, 13,94%), A5 (plan emprendedor, 9,95%), A6 (canal orgánico, 10,58%) y A8 (canal contador_aliado, 10,26%) son cortes del mismo evento: todos saltan el mismo mes y sus excesos suman aproximadamente el exceso de CO. paid_search también sube a 10,53% de fallo sin cruzar umbral. A4 es consecuencia, no causa independiente.

**Hipótesis**
1. Incidencia en la pasarela o en el procesamiento de tarjetas/débito local en Colombia durante marzo (rechazos técnicos concentrados en un método o emisor), dado que el salto es de un solo mes, afecta a todos los planes y canales por igual y no aparece en otros países.
2. Cambio operativo del lado nuestro en CO (nuevo proveedor de cobro, reglas antifraude, moneda o datos de facturación) que eleva rechazos en primer intento, sin problema real de solvencia del cliente.
3. Deterioro genuino de la capacidad de pago de la base colombiana; menos probable porque el churn voluntario de CO no se movió y el salto es abrupto en vez de gradual.

**Para confirmar:** Abrir los 52 fallos de CO por pasarela, método de pago, emisor y código de error, y compararlos con febrero; además medir la tasa de recuperación en reintentos: si los reintentos cobran sin cambio de medio de pago, es un problema técnico de cobro y no de cliente. Vigilar el churn involuntario de CO en abril por el rezago de los fallos de marzo.

## [MEDIA] Conversión trial→pago de paid_search estancada en la mitad de su nivel desde enero

Los últimos 3 meses agrupados de paid_search convierten 15,03% (26 de 173) frente a 28,25% de los 6 meses previos: ~23 clientes pagados menos de lo esperado. Mensual: 36,07% en diciembre y luego 11,67%, 16,67% y 17,02%. Es un cambio de nivel sostenido, no un mes suelto. El total se mantiene en 36,96% porque orgánico (41,38%) y el resto compensan.

*Señales relacionadas:* La conversión total (31,07%, 31,22%, 36,96% desde enero) está por debajo del 39-43% de octubre-diciembre, arrastrada sobre todo por este canal. contador_aliado bajó a 38,1% en marzo, pero sin alerta y con base chica.

**Hipótesis**
1. Cambio en la mezcla de campañas/keywords o en el mix de países desde enero que trae tráfico de peor intención: mismo volumen de registros, peor calidad.
2. Mayor volumen comprado a costa de audiencias más frías (revisar si den por mes creció frente a 2025).
3. Fricción nueva en el flujo de alta/checkout que golpea más a usuarios de paid_search, menos familiarizados con el producto.

**Para confirmar:** Comparar registros y conversión de paid_search por campaña/keyword y país entre el trimestre oct-dic y ene-mar, y contrastar señales de activación temprana del trial (empresas que cargan datos o emiten primer documento) para distinguir tráfico de peor calidad de un problema en el flujo de alta.

## Probable ruido

A2, A3, A5, A6 y A8 no son hallazgos independientes: son el mismo pico de fallo de pagos de marzo visto por plan, canal y total, y su exceso se explica por CO; A8 además tiene z apenas 3,18 y base pequeña (156 intentos). A4 se reporta dentro del mismo bloque por ser efecto directo de los cobros fallidos. La caída de conversión de contador_aliado en marzo (38,1% vs 50-63% previos) no generó alerta y con ~21 registros al mes es demasiado volátil para concluir; solo merece seguimiento.
