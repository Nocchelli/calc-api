import pdfplumber
import io
import re
from datetime import datetime
from app.extractors.base import ExtratorBase


class ExtratorLeeEmpreendimentos(ExtratorBase):
    """Extrator para os relatórios Sankhya da Lee Empreendimentos.

    A Lee emite quatro relatórios distintos para o mesmo contrato, cada um com
    sua própria tabela de colunas:
      - "Parcelas em Aberto": todas as parcelas não pagas (vencidas ou não),
        cujo valor a considerar é a coluna "Vlr Presente".
      - "Parcelas Vencidas": apenas as parcelas em atraso, com detalhamento
        de juros/multa/correção, cujo valor a considerar é "Vlr a Pagar".
      - "Parcelas Pagas Atualizadas": histórico de parcelas já quitadas,
        cujo valor a considerar é "Vlr. Pago".
      - "Parcelas por Contrato": todo o plano de parcelas (pagas, vencidas e a
        vencer) num único relatório. Datas em formato de 2 dígitos (dd/mm/aa),
        a coluna "Pagto" só aparece quando a parcela já foi paga, e a linha de
        MFD/taxa vem como "MF" (sem número de parcela/renegociação).

    O tipo de relatório é detectado pelo título da página, e cada um usa seu
    próprio padrão de linha. As linhas da tabela de parcelas saem do
    pdfplumber em ordem visual estável (uma linha de texto por parcela), mas
    o bloco de dados do contrato (cabeçalho em formato de formulário de duas
    colunas) sai embaralhado — por isso ele é reconstruído a partir da
    posição x/y das palavras, como no extrator da Viver Bem.
    """

    _NUM = r'\d{1,3}(?:\.\d{3})*,\d{2}'

    PADRAO_TIPO_ABERTO = re.compile(r'Parcelas\s+em\s+Aberto', re.IGNORECASE)
    PADRAO_TIPO_VENCIDA = re.compile(r'Parcelas\s+Vencidas', re.IGNORECASE)
    PADRAO_TIPO_PAGA = re.compile(r'Parcelas\s+Pagas\s+Atualizadas', re.IGNORECASE)
    PADRAO_TIPO_CONTRATO = re.compile(r'Parcelas\s+por\s+Contrato', re.IGNORECASE)

    # Nº Parc/Ren | Dt. Venc | Vlr. Parcela | Vlr. Outros | Vlr Presente | Vlr Diferença
    # O grupo "ren" é opcional porque, em alguns PDFs, o pdfplumber não detecta
    # espaço entre o número da parcela e o código de renegociação (ex.: "25200"
    # em vez de "25 200"). Nesse caso o "parc" ganancioso absorve os dois
    # números colados e eles são separados depois, em _dividir_parc_ren.
    PADRAO_LINHA_ABERTO = re.compile(
        r'^(?P<parc>\d+)(?:\s+(?P<ren>\d+))?\s+'
        r'(?P<vencimento>\d{2}/\d{2}/\d{4})\s+'
        rf'(?P<vlr_parcela>{_NUM})\s+'
        rf'(?P<vlr_outros>{_NUM})\s+'
        rf'(?P<vlr_presente>{_NUM})\s+'
        rf'(?P<vlr_diferenca>-?{_NUM})\s*$'
    )

    # Nº Parc/Ren | Dt. Venc. Ini. | Qtd. dias atraso | Vlr. Parcela | Vlr. Outros
    # | Vlr. Corr. Monet. | Vlr. Juros | Vlr. Multa | Vlr a Pagar
    PADRAO_LINHA_VENCIDA = re.compile(
        r'^(?P<parc>\d+)(?:\s+(?P<ren>\d+))?\s+'
        r'(?P<vencimento>\d{2}/\d{2}/\d{4})\s+'
        r'(?P<dias_atraso>\d+)\s+'
        rf'(?P<vlr_parcela>{_NUM})\s+'
        rf'(?P<vlr_outros>{_NUM})\s+'
        rf'(?P<vlr_corr_monet>{_NUM})\s+'
        rf'(?P<vlr_juros>{_NUM})\s+'
        rf'(?P<vlr_multa>{_NUM})\s+'
        rf'(?P<vlr_a_pagar>{_NUM})\s*$'
    )

    # Parc/Ren (ou "Mfd") | Vencto | Pagto | Atraso | Parcela | Vlr. Taxa
    # | Vlr. MFD | Vlr. Juros | Vlr. Multa | Vlr. Desconto | Vlr. Pago
    PADRAO_LINHA_PAGA = re.compile(
        r'^(?:(?P<parc>\d+)(?:\s+(?P<ren>\d+))?|(?P<mfd>Mfd))\s+'
        r'(?P<vencimento>\d{2}/\d{2}/\d{4})\s+'
        r'(?P<pagamento>\d{2}/\d{2}/\d{4})\s+'
        r'(?P<atraso>\d+)\s+'
        rf'(?P<vlr_parcela>{_NUM})\s+'
        rf'(?P<vlr_taxa>{_NUM})\s+'
        rf'(?P<vlr_mfd>{_NUM})\s+'
        rf'(?P<vlr_juros>{_NUM})\s+'
        rf'(?P<vlr_multa>{_NUM})\s+'
        rf'(?P<vlr_desconto>{_NUM})\s+'
        rf'(?P<vlr_pago>{_NUM})\s*$'
    )

    # Parc/Ren (ou "MF") | Vencto | Pagto (opcional - só quando já paga) | Atraso
    # | Entrada | Parcela | Outros | Atu. Monet. | Juros | Multa
    # | Vlr.Bx - Vlr.Parc | A Pagar | Pago
    PADRAO_LINHA_CONTRATO = re.compile(
        r'^(?:(?P<parc>\d+)\s+(?P<ren>\S+)|(?P<mf>MF))\s+'
        r'(?P<vencimento>\d{2}/\d{2}/\d{2,4})\s+'
        r'(?:(?P<pagamento>\d{2}/\d{2}/\d{2,4})\s+)?'
        r'(?P<atraso>\d+)\s+'
        rf'(?P<vlr_entrada>{_NUM})\s+'
        rf'(?P<vlr_parcela>{_NUM})\s+'
        rf'(?P<vlr_outros>{_NUM})\s+'
        rf'(?P<vlr_atu_monet>{_NUM})\s+'
        rf'(?P<vlr_juros>{_NUM})\s+'
        rf'(?P<vlr_multa>{_NUM})\s+'
        rf'(?P<vlr_bx>{_NUM})\s+'
        rf'(?P<vlr_a_pagar>{_NUM})\s+'
        rf'(?P<vlr_pago>{_NUM})\s*$'
    )

    PADRAO_CODIGO = re.compile(r'C[oó]digo:\s*([\d.]+)', re.IGNORECASE)
    PADRAO_QUADRA = re.compile(r'Quadra:\s*(\S+)', re.IGNORECASE)
    PADRAO_LOTEAMENTO = re.compile(r'Loteamento:\s*(.+?)\s+Lote:', re.IGNORECASE)
    PADRAO_LOTE = re.compile(r'\bLote:\s*(\S+)', re.IGNORECASE)
    PADRAO_LOTEADOR = re.compile(r'Loteador:\s*(.+?)\s+Tabela:', re.IGNORECASE)
    PADRAO_TABELA = re.compile(r'Tabela:\s*(.+)', re.IGNORECASE)
    PADRAO_COMPRADORES = re.compile(r'Compradores:\s*(.+?)\s+\d{3}\.\d{3}\.\d{3}-\d{2}', re.IGNORECASE)
    PADRAO_CPF = re.compile(r'(\d{3}\.\d{3}\.\d{3}-\d{2})')
    PADRAO_TELEFONE = re.compile(r'\d{3}\.\d{3}\.\d{3}-\d{2}\s+([\d/]{8,})')
    PADRAO_ESTAGIO = re.compile(r'Est[aá]gio:\s*(.+?)\s*Dt da venda:', re.IGNORECASE)
    PADRAO_DT_VENDA = re.compile(r'Dt da venda:\s*(\d{2}/\d{2}/\d{4})', re.IGNORECASE)
    PADRAO_VLR_VENDA = re.compile(r'Vlr\.?\s*Venda:\s*R\$\s*([\d.,]+)', re.IGNORECASE)
    PADRAO_PLANO = re.compile(r'Plano:\s*(\S+)', re.IGNORECASE)

    def extrair(self, conteudo_arquivo: bytes) -> dict:
        parcelas = []
        detalhes_contrato = {
            "cliente": "Não identificado",
            "cpf_cnpj": "",
            "telefones": "",
            "endereco": "",
            "codigo": "",
            "loteamento": "",
            "loteador": "",
            "quadra": "",
            "lote": "",
            "tabela": "",
            "estagio": "",
            "dt_venda": "",
            "vlr_venda": "",
            "plano": "",
        }

        tipo_relatorio = None
        hoje = datetime.now().date()

        with pdfplumber.open(io.BytesIO(conteudo_arquivo)) as pdf:
            for pagina in pdf.pages:
                texto = pagina.extract_text()
                if not texto:
                    continue

                if tipo_relatorio is None:
                    tipo_relatorio = self._detectar_tipo_relatorio(texto)

                self._extrair_detalhes_contrato(pagina, detalhes_contrato)

                for linha in texto.split("\n"):
                    parcela = self._parse_linha_parcela(
                        linha.strip(), tipo_relatorio, hoje, detalhes_contrato.get("plano")
                    )
                    if parcela:
                        parcelas.append(parcela)

        if tipo_relatorio is None:
            return {
                "erro": (
                    "Não foi possível identificar o tipo de relatório da Lee "
                    "Empreendimentos (Parcelas em Aberto, Parcelas Vencidas, "
                    "Parcelas Pagas Atualizadas ou Parcelas por Contrato)."
                )
            }

        if not parcelas:
            return {
                "erro": (
                    "Não foi possível localizar a tabela de parcelas no PDF: "
                    f"nenhuma linha no layout esperado de '{tipo_relatorio}' foi encontrada."
                )
            }

        return {"contrato": detalhes_contrato, "parcelas": parcelas, "cliente": detalhes_contrato["cliente"]}

    def _detectar_tipo_relatorio(self, texto):
        if self.PADRAO_TIPO_PAGA.search(texto):
            return "Parcelas Pagas Atualizadas"
        if self.PADRAO_TIPO_CONTRATO.search(texto):
            return "Parcelas por Contrato"
        if self.PADRAO_TIPO_VENCIDA.search(texto):
            return "Parcelas Vencidas"
        if self.PADRAO_TIPO_ABERTO.search(texto):
            return "Parcelas em Aberto"
        return None

    def _parse_linha_parcela(self, linha, tipo_relatorio, hoje, plano=None):
        if tipo_relatorio == "Parcelas em Aberto":
            m = self.PADRAO_LINHA_ABERTO.match(linha)
            if not m:
                return None
            vencimento_date = self._parse_data(m.group("vencimento"))
            if vencimento_date is None:
                return None
            status = "Vencido" if vencimento_date < hoje else "A vencer"
            parc, ren = self._dividir_parc_ren(m.group("parc"), m.group("ren"), plano)
            return {
                "parcela": parc,
                "plano": ren,
                "vencimento": m.group("vencimento"),
                "valor_parcela": self._parse_valor(m.group("vlr_parcela")),
                "vlr_outros": self._parse_valor(m.group("vlr_outros")),
                "vlr_presente": self._parse_valor(m.group("vlr_presente")),
                "vlr_diferenca": self._parse_valor(m.group("vlr_diferenca")),
                "valor_original": self._parse_valor(m.group("vlr_presente")),
                "status": status,
                "data_pagamento": None,
            }

        if tipo_relatorio == "Parcelas Vencidas":
            m = self.PADRAO_LINHA_VENCIDA.match(linha)
            if not m:
                return None
            if self._parse_data(m.group("vencimento")) is None:
                return None
            parc, ren = self._dividir_parc_ren(m.group("parc"), m.group("ren"), plano)
            return {
                "parcela": parc,
                "plano": ren,
                "vencimento": m.group("vencimento"),
                "dias_atraso": int(m.group("dias_atraso")),
                "valor_parcela": self._parse_valor(m.group("vlr_parcela")),
                "vlr_outros": self._parse_valor(m.group("vlr_outros")),
                "vlr_corr_monet": self._parse_valor(m.group("vlr_corr_monet")),
                "vlr_juros": self._parse_valor(m.group("vlr_juros")),
                "vlr_multa": self._parse_valor(m.group("vlr_multa")),
                "vlr_a_pagar": self._parse_valor(m.group("vlr_a_pagar")),
                "valor_original": self._parse_valor(m.group("vlr_a_pagar")),
                "status": "Vencido",
                "data_pagamento": None,
            }

        if tipo_relatorio == "Parcelas Pagas Atualizadas":
            m = self.PADRAO_LINHA_PAGA.match(linha)
            if not m:
                return None
            if self._parse_data(m.group("vencimento")) is None or self._parse_data(m.group("pagamento")) is None:
                return None
            if m.group("mfd"):
                parc, ren = m.group("mfd"), ""
            else:
                parc, ren = self._dividir_parc_ren(m.group("parc"), m.group("ren"), plano)
            return {
                "parcela": parc,
                "plano": ren,
                "vencimento": m.group("vencimento"),
                "atraso": int(m.group("atraso")),
                "valor_parcela": self._parse_valor(m.group("vlr_parcela")),
                "vlr_taxa": self._parse_valor(m.group("vlr_taxa")),
                "vlr_mfd": self._parse_valor(m.group("vlr_mfd")),
                "vlr_juros": self._parse_valor(m.group("vlr_juros")),
                "vlr_multa": self._parse_valor(m.group("vlr_multa")),
                "vlr_desconto": self._parse_valor(m.group("vlr_desconto")),
                "vlr_pago": self._parse_valor(m.group("vlr_pago")),
                "valor_original": self._parse_valor(m.group("vlr_pago")),
                "status": "Pago",
                "data_pagamento": m.group("pagamento"),
            }

        if tipo_relatorio == "Parcelas por Contrato":
            m = self.PADRAO_LINHA_CONTRATO.match(linha)
            if not m:
                return None
            vencimento_date = self._parse_data(m.group("vencimento"))
            if vencimento_date is None:
                return None
            pagamento = m.group("pagamento")
            if pagamento:
                status = "Pago"
                valor_original = self._parse_valor(m.group("vlr_pago"))
            else:
                status = "Vencido" if vencimento_date < hoje else "A vencer"
                valor_original = self._parse_valor(m.group("vlr_a_pagar"))
            if m.group("mf"):
                parc, ren = m.group("mf"), ""
            else:
                parc, ren = self._dividir_parc_ren(m.group("parc"), m.group("ren"), plano)
            return {
                "parcela": parc,
                "plano": ren,
                "vencimento": m.group("vencimento"),
                "atraso": int(m.group("atraso")),
                "vlr_entrada": self._parse_valor(m.group("vlr_entrada")),
                "valor_parcela": self._parse_valor(m.group("vlr_parcela")),
                "vlr_outros": self._parse_valor(m.group("vlr_outros")),
                "vlr_atu_monet": self._parse_valor(m.group("vlr_atu_monet")),
                "vlr_juros": self._parse_valor(m.group("vlr_juros")),
                "vlr_multa": self._parse_valor(m.group("vlr_multa")),
                "vlr_bx": self._parse_valor(m.group("vlr_bx")),
                "vlr_a_pagar": self._parse_valor(m.group("vlr_a_pagar")),
                "vlr_pago": self._parse_valor(m.group("vlr_pago")),
                "valor_original": valor_original,
                "status": status,
                "data_pagamento": pagamento,
            }

        return None

    def _extrair_detalhes_contrato(self, pagina, detalhes_contrato):
        palavras = pagina.extract_words(use_text_flow=False, keep_blank_chars=False)
        if not palavras:
            return

        linhas_palavras = self._agrupar_por_linha(palavras)
        linhas_texto = [" ".join(p["text"] for p in linha) for linha in linhas_palavras]
        texto_reconstruido = "\n".join(linhas_texto)

        m = self.PADRAO_CODIGO.search(texto_reconstruido)
        if m:
            detalhes_contrato["codigo"] = m.group(1).strip()

        m = self.PADRAO_QUADRA.search(texto_reconstruido)
        if m:
            detalhes_contrato["quadra"] = m.group(1).strip()

        m = self.PADRAO_LOTEAMENTO.search(texto_reconstruido)
        if m:
            detalhes_contrato["loteamento"] = m.group(1).strip()

        m = self.PADRAO_LOTE.search(texto_reconstruido)
        if m:
            detalhes_contrato["lote"] = m.group(1).strip()

        m = self.PADRAO_LOTEADOR.search(texto_reconstruido)
        if m:
            detalhes_contrato["loteador"] = m.group(1).strip()

        m = self.PADRAO_TABELA.search(texto_reconstruido)
        if m:
            detalhes_contrato["tabela"] = m.group(1).strip()

        m = self.PADRAO_COMPRADORES.search(texto_reconstruido)
        if m:
            detalhes_contrato["cliente"] = m.group(1).strip()

        m = self.PADRAO_CPF.search(texto_reconstruido)
        if m:
            detalhes_contrato["cpf_cnpj"] = m.group(1).strip()

        m = self.PADRAO_TELEFONE.search(texto_reconstruido)
        if m:
            detalhes_contrato["telefones"] = m.group(1).strip()

        m = self.PADRAO_ESTAGIO.search(texto_reconstruido)
        if m:
            detalhes_contrato["estagio"] = m.group(1).strip()

        m = self.PADRAO_DT_VENDA.search(texto_reconstruido)
        if m:
            detalhes_contrato["dt_venda"] = m.group(1).strip()

        m = self.PADRAO_VLR_VENDA.search(texto_reconstruido)
        if m:
            detalhes_contrato["vlr_venda"] = m.group(1).strip()

        m = self.PADRAO_PLANO.search(texto_reconstruido)
        if m:
            detalhes_contrato["plano"] = m.group(1).strip()

        for i, linha in enumerate(linhas_texto):
            if "Compradores:" in linha and i + 1 < len(linhas_texto):
                proxima = linhas_texto[i + 1]
                if ":" not in proxima:
                    detalhes_contrato["endereco"] = proxima.strip()

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

    @staticmethod
    def _dividir_parc_ren(bloco, ren, plano):
        """Separa o número da parcela do código de renegociação quando o
        pdfplumber não detectou espaço entre os dois (ex.: "25200" -> "25" +
        "200"). Se o regex já capturou "ren" separadamente, não há o que
        fazer. Caso contrário, usa o "Plano" do cabeçalho (igual ao "ren" em
        todas as linhas do relatório) como sufixo para dividir o bloco; na
        ausência dele, assume que "ren" tem 3 dígitos, como observado nestes
        relatórios.
        """
        if ren:
            return bloco, ren
        if plano and bloco.endswith(plano) and len(bloco) > len(plano):
            return bloco[:-len(plano)], plano
        if len(bloco) > 3:
            return bloco[:-3], bloco[-3:]
        return bloco, ""

    @staticmethod
    def _parse_data(data_str):
        for formato in ("%d/%m/%Y", "%d/%m/%y"):
            try:
                return datetime.strptime(data_str, formato).date()
            except (ValueError, TypeError):
                continue
        return None

    @staticmethod
    def _parse_valor(valor_str) -> float:
        """Converte string monetária no formato brasileiro (1.234,56) para float."""
        try:
            valor_limpo = valor_str.replace(".", "").replace(",", ".")
            return float(valor_limpo)
        except (ValueError, AttributeError, TypeError):
            return 0.0
