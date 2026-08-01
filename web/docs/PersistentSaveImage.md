# Persistent Save Image

Salva imagens usando o mesmo backend do **Save Image** e mantém no node uma galeria persistente com as imagens mais recentes.

- Funciona com batch e com vários prompts na fila.
- A galeria é recuperada ao voltar para o workflow.
- **Reset Galleria** limpa as miniaturas persistidas, mas não apaga os PNGs da pasta `output`.
- O limite é definido por `MAX_GALLERY_IMAGES` no arquivo `config.py`.
