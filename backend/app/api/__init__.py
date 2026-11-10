"""Capa de API: routers HTTP sobre los módulos del núcleo.

Los routers no contienen lógica de negocio. Traducen un pedido HTTP a una
llamada a un módulo y la respuesta a un esquema de salida. Si una regla
termina escrita acá en lugar de en su módulo, el único camino para
ejercitarla pasa a ser la red, y deja de poder probarse sola.
"""
