# Carrinho vazio mostra total de R$ 7,90

Quando o cliente remove todos os itens do carrinho, a tela de resumo continua mostrando a taxa
de entrega: subtotal R$ 0,00, entrega R$ 7,90, total R$ 7,90.

Com o carrinho vazio não tem nada para entregar, então a taxa de entrega e o total deveriam
ser R$ 0,00. O resumo vem da função `calculate_order` chamada com a lista de itens vazia.
