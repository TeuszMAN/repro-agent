# Sistema aceita cupom percentual acima de 100%

Ao cadastrar os cupons da promoção, digitei sem querer 120 em vez de 12 num cupom do tipo
percentual. O sistema aceitou o cupom sem reclamar. Um cliente usou e o pedido saiu de graça,
pagando só a entrega.

Cupom percentual com valor acima de 100 deveria ser recusado na criação, com `ValueError`,
igual já acontece quando o valor do cupom é zero ou negativo.

Para reproduzir:

```python
from decimal import Decimal
from pedido import Coupon, CouponKind

Coupon(code="PROMO12", kind=CouponKind.PERCENTAGE, value=Decimal("120"))  # não dá erro
```
