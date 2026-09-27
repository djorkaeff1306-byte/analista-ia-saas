# Alertas del periodo 2026-06

**Salto fuerte del churn voluntario en junio (7,49% vs 4,21% de base, +27 clientes sobre lo esperado) prácticamente todo concentrado en México, sin señales de problemas de cobro.**

## [ALTA] Pico de churn voluntario concentrado en México

En MX el churn voluntario de clientes pasó a 17,95% (35 bajas sobre 195 activos al inicio) frente a una base de 4,36%, unos 26,5 clientes por encima de lo esperado (z=5,32). El total subió a 7,49% (62/828) contra 4,21%, con un exceso de ~27 clientes: es decir, casi todo el exceso del total es México (fuera de MX son 27 bajas sobre 633 activos, ~4,3%, en línea con la base). El churn involuntario no se movió (MX 3,08% vs 3,11% en mayo; total 1,81%) y la tasa de fallo de pagos incluso bajó en MX (6,67% vs 7,96%), así que no es un problema de cobro sino de decisión de los clientes.

*Señales relacionadas:* A2 (total), A3 (plan pyme: 9,18%, 28/305), A4 (canal orgánico: 7,51%, 25/333) y A5 (canal contador_aliado: 11,04%, 18/163) son vistas de la misma cohorte: el exceso combinado de plan y canales no supera el exceso de MX, por lo que se explican por concentración geográfica más que por un problema propio de plan o canal. Conversión trial→pago en MX (34,69%) y total (32,08%) dentro de su rango histórico; no hay alerta asociada.

**Hipótesis**
1. Cambio comercial o de precio específico de México (ajuste de tarifa, fin de promoción o migración de plan) que disparó cancelaciones voluntarias en un solo mes.
2. Evento de producto/regulatorio local: un cambio normativo contable o fiscal en MX que el producto no cubrió a tiempo, o una funcionalidad clave degradada para ese mercado, empujando a clientes a un competidor.
3. Efecto de mezcla/cohorte: entrada masiva previa de clientes MX de baja calidad (por ejemplo vía contador aliado y orgánico, donde la tasa es más alta) que llegaron juntos al final de su ciclo de compromiso.

**Para confirmar:** Abrir las 35 bajas voluntarias de MX por motivo declarado de cancelación, fecha de alta (cohorte), plan y si tuvieron cambio de precio en los últimos 90 días; comparar contra las bajas voluntarias del resto de países del mismo mes.

## Probable ruido

No hay alertas sueltas atribuibles a azar: las cinco corresponden a la misma métrica y mes. A3, A4 y A5 tienen z entre 3,2 y 4,3 y se explican aritméticamente por el bloque de México; conviene no abrir iniciativas separadas por plan o canal hasta descartar el efecto país. El churn involuntario y la tasa de fallo de pagos no generaron alertas y se mantienen en niveles normales.
