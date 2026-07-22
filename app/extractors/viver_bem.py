import pdfplumber
import io
import re
from datetime import datetime
from app.extractors.base import ExtratorBase


class ExtratorViverBem(ExtratorBase):

    # Campos sem os quais não é possível calcular a parcela.
    # nome interno -> rótulo amigável usado na mensagem de erro
    CAMPOS_OBRIGATORIOS = {
        "vencimento": "Dt Vencim (data de vencimento)",
        "valor_original": "Valor Parc. (valor da parcela)",
        "dt_receb": "Dt. Receb. (data de recebimento)",
    }

    # Colunas extras opcionais: quando o PDF não trouxer alguma delas,
    # o valor é preenchido com 0,00.
    CAMPOS_EXTRAS = [
        "juros", "correcao", "multa", "juros_atraso",
        "acrescimo", "desconto_antecipado", "outros"
    ]

    # Reconhece cada campo pelo texto do cabeçalho da tabela, tolerando falta
    # de acentuação (comum em texto extraído de PDF) e pequenas variações de
    # abreviação. Alternativas mais específicas vêm antes das mais genéricas
    # (ex.: "Juros Atr." antes de "Juros" isolado) para não haver ambiguidade.
    PADRAO_CAMPOS = re.compile(
        r'(?P<juros_atraso>Juros\s*Atr[a-z\.]*)'
        r'|(?P<correcao>Corre[cç][aã]o)'
        r'|(?P<multa>Multa)'
        r'|(?P<acrescimo>Acr[eé]sc?[a-z\.]*)'
        r'|(?P<desconto_antecipado>Desc\.?\s*Ant[a-z\.]*)'
        r'|(?P<outros>Outros?)'
        r'|(?P<vlr_parcela>Vlr\.?\s*(da\s*)?Parcela)'
        r'|(?P<valor_original>Valor\s*Parc[a-z\.]*)'
        r'|(?P<dt_receb>Dt\.?\s*Receb[a-z\.]*)'
        r'|(?P<vencimento>Dt\.?\s*Vencim[a-z\.]*)'
        r'|(?P<descricao>Descri[cç][aã]o)'
        r'|(?P<parcela>Parcela)'
        r'|(?P<juros>Juros)',
        re.IGNORECASE
    )

    def extrair(self, conteudo_arquivo: bytes) -> dict:
        parcelas = []
        detalhes_contrato = {
            "cliente": "Não identificado",
            "venda": "",
            "empreendimento": "",
            "telefones": "",
            "endereco": "",
            "cidade_uf": ""
        }

        padrao_cliente = re.compile(r'Cliente\s*:\s*\d+\s*-\s*(.+)')
        padrao_venda = re.compile(r'Venda:\s*(\d+)')
        padrao_empreend = re.compile(r'Empreend\.:\s*(.+)')
        padrao_fones = re.compile(r'Fones:\s*Residencial:\s*(.*?)\s*Celular:\s*(.*?)\s*Comercial:')
        padrao_end = re.compile(r'End\.:\s*(.+?)\s+No\.')
        padrao_cidade = re.compile(r'Cidade\s*:\s*(.+?)\s+UF\s*:\s*(.+?)\s+CEP')

        padrao_linha_parcela = re.compile(r'^(P\.\d+|E\.\d+)\/\d+')
        # Busca (não match estrito) porque, em alguns PDFs, o cabeçalho gruda o rótulo
        # de uma coluna vizinha (ex.: "VencimAtraso" sem espaço) sem que isso apareça
        # como uma palavra separada — nesse caso a zona de "vencimento" fica larga
        # demais e absorve o valor da coluna "Atraso" da linha de dados junto com a
        # data. Buscando a data dentro do texto (em vez de exigir igualdade exata)
        # a extração continua funcionando mesmo com esse ruído extra na zona.
        padrao_data = re.compile(r'\d{2}/\d{2}/\d{4}')

        zonas_colunas = None  # dict: nome_campo -> (x_esquerda, x_direita)
        hoje = datetime.now().date()

        with pdfplumber.open(io.BytesIO(conteudo_arquivo)) as pdf:
            for pagina in pdf.pages:

                texto = pagina.extract_text()
                if not texto:
                    continue

                m_cli = padrao_cliente.search(texto)
                if m_cli:
                    detalhes_contrato["cliente"] = m_cli.group(1).strip()

                m_ven = padrao_venda.search(texto)
                if m_ven:
                    detalhes_contrato["venda"] = m_ven.group(1).strip()

                m_emp = padrao_empreend.search(texto)
                if m_emp:
                    detalhes_contrato["empreendimento"] = m_emp.group(1).strip()

                m_fon = padrao_fones.search(texto)
                if m_fon:
                    detalhes_contrato["telefones"] = f"Res: {m_fon.group(1)} | Cel: {m_fon.group(2)}"

                m_end = padrao_end.search(texto)
                if m_end:
                    detalhes_contrato["endereco"] = m_end.group(1).strip()

                m_cid = padrao_cidade.search(texto)
                if m_cid:
                    detalhes_contrato["cidade_uf"] = f"{m_cid.group(1)} / {m_cid.group(2)}"

                palavras = pagina.extract_words(use_text_flow=False, keep_blank_chars=False)
                if not palavras:
                    continue
                linhas_palavras = self._agrupar_por_linha(palavras)

                if zonas_colunas is None:
                    zonas_colunas = self._detectar_zonas_colunas(linhas_palavras)
                    if zonas_colunas is None:
                        continue  # cabeçalho da tabela ainda não apareceu nesta página

                for linha_palavras in linhas_palavras:
                    if not linha_palavras:
                        continue
                    codigo_parcela = linha_palavras[0]["text"]
                    if not padrao_linha_parcela.match(codigo_parcela):
                        continue

                    valores = self._valores_por_zona(linha_palavras, zonas_colunas)

                    data_vencimento_bruta = valores.get("vencimento")
                    valor_original_str = valores.get("valor_original")
                    if not data_vencimento_bruta or not valor_original_str:
                        continue
                    m_venc = padrao_data.search(data_vencimento_bruta)
                    if not m_venc:
                        continue
                    data_vencimento = m_venc.group()

                    try:
                        vencimento_date = datetime.strptime(data_vencimento, "%d/%m/%Y").date()
                    except ValueError:
                        continue

                    valor_original_float = self._parse_valor(valor_original_str)

                    dt_receb_str = valores.get("dt_receb")
                    m_receb = padrao_data.search(dt_receb_str) if dt_receb_str else None
                    if m_receb:
                        data_pagamento = m_receb.group()
                        status_parcela = "Pago"
                    else:
                        data_pagamento = None
                        status_parcela = "Vencido" if vencimento_date < hoje else "A vencer"

                    parcela = {
                        "parcela": codigo_parcela,
                        "vencimento": data_vencimento,
                        "valor_original": valor_original_float,
                        "status": status_parcela,
                        "data_pagamento": data_pagamento,
                    }
                    for nome_campo in self.CAMPOS_EXTRAS:
                        valor_str = valores.get(nome_campo)
                        parcela[nome_campo] = self._parse_valor(valor_str) if valor_str else 0.0

                    parcelas.append(parcela)

        if zonas_colunas is None:
            return {
                "erro": (
                    "Não foi possível localizar a tabela de parcelas no PDF: "
                    "o cabeçalho com as colunas de vencimento e recebimento não foi encontrado."
                )
            }

        faltando = [
            rotulo for campo, rotulo in self.CAMPOS_OBRIGATORIOS.items()
            if campo not in zonas_colunas
        ]
        if faltando:
            return {
                "erro": (
                    "O PDF não contém as colunas obrigatórias para o cálculo: "
                    + "; ".join(faltando) + "."
                )
            }

        return {"contrato": detalhes_contrato, "parcelas": parcelas, "cliente": detalhes_contrato["cliente"]}

    @staticmethod
    def _agrupar_por_linha(palavras, tolerancia=3):
        """Agrupa palavras extraídas do PDF em linhas, pela coordenada vertical."""
        linhas = []
        atual = []
        for p in sorted(palavras, key=lambda w: (w["top"], w["x0"])):
            if atual and (p["top"] - atual[0]["top"]) > tolerancia:
                linhas.append(atual)
                atual = []
            atual.append(p)
        if atual:
            linhas.append(atual)
        for linha in linhas:
            linha.sort(key=lambda w: w["x0"])
        return linhas

    def _detectar_zonas_colunas(self, linhas_palavras):
        """
        Localiza a linha de cabeçalho da tabela de parcelas e monta, para cada
        campo reconhecido, a faixa horizontal (x0, x1) onde seu valor aparece
        nas linhas de dados — usando a posição real das palavras no PDF, e não
        a ordem ou a quantidade de colunas.

        Isso permite que o PDF tenha qualquer número de colunas (conhecidas ou
        não) em qualquer ordem, e tolera colunas que ficam em branco em
        algumas linhas (ex.: uma coluna "Atraso" que só aparece quando há
        dias de atraso).
        """
        for linha in linhas_palavras:
            texto_linha = " ".join(p["text"] for p in linha)
            if not re.search(r'Vencim', texto_linha, re.IGNORECASE):
                continue
            if not re.search(r'Receb', texto_linha, re.IGNORECASE):
                continue

            texto_montado = ""
            offsets = []
            for p in linha:
                if texto_montado:
                    texto_montado += " "
                inicio = len(texto_montado)
                texto_montado += p["text"]
                offsets.append((inicio, len(texto_montado), p))

            # Percorre o cabeçalho marcando, em ordem, tanto os campos
            # reconhecidos quanto os "buracos" (colunas desconhecidas) — para
            # que estes últimos reservem seu espaço e não sejam engolidos
            # pelas colunas vizinhas conhecidas.
            pontos = []  # lista de (x0, x1, nome_campo_ou_None)
            cursor = 0
            for m in self.PADRAO_CAMPOS.finditer(texto_montado):
                for (s, e, p) in offsets:
                    if s >= cursor and e <= m.start():
                        pontos.append((p["x0"], p["x1"], None))
                palavras_do_match = [p for (s, e, p) in offsets if s < m.end() and e > m.start()]
                if palavras_do_match:
                    x0 = min(p["x0"] for p in palavras_do_match)
                    x1 = max(p["x1"] for p in palavras_do_match)
                    pontos.append((x0, x1, m.lastgroup))
                cursor = max(cursor, m.end())
            for (s, e, p) in offsets:
                if s >= cursor:
                    pontos.append((p["x0"], p["x1"], None))

            if not pontos:
                continue
            pontos.sort(key=lambda t: t[0])

            # O limite entre duas colunas fica no meio do vão entre a borda
            # direita de uma e a borda esquerda da próxima — não no meio dos
            # x0 dos rótulos do cabeçalho. Isso importa porque valores
            # monetários costumam ser alinhados à direita dentro da coluna
            # (bem mais à direita do que o próprio rótulo do cabeçalho),
            # então usar só os x0 deixaria a zona estreita demais e jogaria
            # o valor real para a coluna vizinha.
            zonas = {}
            for i, (x0, x1, nome_campo) in enumerate(pontos):
                if nome_campo is None:
                    continue
                esq = float("-inf") if i == 0 else (pontos[i - 1][1] + x0) / 2
                dir_ = float("inf") if i == len(pontos) - 1 else (x1 + pontos[i + 1][0]) / 2
                zonas[nome_campo] = (esq, dir_)

            if "vencimento" in zonas and "dt_receb" in zonas:
                return zonas

        return None

    @staticmethod
    def _valores_por_zona(linha_palavras, zonas):
        valores = {nome: [] for nome in zonas}
        for p in linha_palavras:
            centro = (p["x0"] + p["x1"]) / 2
            for nome_campo, (esq, dir_) in zonas.items():
                if esq <= centro < dir_:
                    valores[nome_campo].append(p["text"])
                    break
        return {nome: " ".join(txts) if txts else None for nome, txts in valores.items()}

    @staticmethod
    def _parse_valor(valor_str) -> float:
        """Converte string monetária no formato brasileiro (1.234,56) para float."""
        try:
            valor_limpo = valor_str.replace(".", "").replace(",", ".")
            return float(valor_limpo)
        except (ValueError, AttributeError, TypeError):
            return 0.0
