import re


def somente_digitos(texto: str) -> str:
    return re.sub(r"\D", "", texto or "")


def cpf_valido(cpf: str) -> bool:
    d = somente_digitos(cpf)
    if len(d) != 11 or d == d[0] * 11:
        return False
    for n in (9, 10):
        soma = sum(int(d[i]) * (n + 1 - i) for i in range(n))
        if (soma * 10) % 11 % 10 != int(d[n]):
            return False
    return True


def formatar_cpf(cpf: str) -> str:
    d = somente_digitos(cpf)
    return f"{d[:3]}.{d[3:6]}.{d[6:9]}-{d[9:]}" if len(d) == 11 else cpf
