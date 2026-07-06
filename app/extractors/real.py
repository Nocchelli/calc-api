import pdfplumber
import io
import re
from datetime import datetime
from app.extractors.base import ExtratorBase


class ExtratorReal(ExtratorBase):
    """Extrator para o layout 'EXTRATO DO CONTRATO' do Grupo Real Viver.

    Ao contrário do Viver Bem, aqui as linhas de dados saem do PDF já em
    texto corrido e em ordem visual estável (uma linha por parcela, sem
    quebras internas), então a extração é feita por regex de linha em vez
    de zonas de coluna por posição x/y. O cabeçalho da tabela, por outro
    lado, é multi-linha e sai embaralhado do pdfplumber, então os dados do
    contrato são obtidos por buscas isoladas no texto da página.
    """

    CAMPOS_OBRIGATORIOS = {
        "vencimento": "Dt.Vencto (data de vencimento)",
        "valor_original": "Vlr. c/ Acréscimos (valor da parcela)",
    }

    _NUM = r'\d{1,3}(?:\.\d{3})*,\d{2}'

    # Colunas na ordem em que aparecem nas 3 tabelas do extrato (Pagos,
    # Vencidas e A Vencer): Dt.Vencto, Parc., Tipo, Condição (opcional),
    # Renegociado, Vlr.Parcelas, Vlr.Acréscimos, Vlr. c/ Acréscimos,
    # Dias Atraso, Data Baixa (opcional) e Valor Baixa.
    PADRAO_LINHA_PARCELA = re.compile(
        r'^(?P<vencimento>\d{2}/\d{2}/\d{4})\s+'
        r'(?P<parcela>\d+)\s+'
        r'(?P<tipo>[A-Za-zÀ-ÿ]+(?:\s[A-Za-zÀ-ÿ]+)*?)\s+'
        r'(?:(?P<condicao>\d+)\s+)?'
        r'(?P<renegociado>Sim|N[aã]o)\s+'
        rf'(?P<valor_original>{_NUM})\s+'
        rf'(?P<acrescimos>{_NUM})\s+'
        rf'(?P<valor_com_acrescimos>{_NUM})\s+'
        r'(?P<dias_atraso>-?\d+)\s+'
        r'(?:(?P<dt_receb>\d{2}/\d{2}/\d{4})\s+)?'
        rf'(?P<valor_baixa>{_NUM})\s*$'
    )

    PADRAO_CLIENTE = re.compile(r'Cliente\s+\d+\s*-\s*(.+?)\s+CPF/CNPJ', re.IGNORECASE)
    PADRAO_CPF_CNPJ = re.compile(r'CPF/CNPJ\s+([\d./-]+)', re.IGNORECASE)
    PADRAO_LOTEAMENTO = re.compile(r'Loteamento\s+(.+)', re.IGNORECASE)
    PADRAO_QUADRA = re.compile(r'Quadra\s+(\S+)', re.IGNORECASE)
    PADRAO_LOTE = re.compile(r'Lote\s+(\S+)', re.IGNORECASE)
    PADRAO_CONTRATO = re.compile(r'Contrato:\s*(\d+)', re.IGNORECASE)
    PADRAO_PERIODICIDADE = re.compile(r'Periodicidade\s+(\d+)', re.IGNORECASE)
    PADRAO_INDEXADOR = re.compile(r'Indexador\s+(\S+)', re.IGNORECASE)
    PADRAO_JUROS = re.compile(r'Juros\s*\(%\)\s+([\d,]+)', re.IGNORECASE)
    PADRAO_DATA_EMISSAO = re.compile(r'Data Emiss[aã]o\s+(\d{2}/\d{2}/\d{4})', re.IGNORECASE)
    PADRAO_DATA_BASE = re.compile(r'Data base corre[cç][aã]o\s+(\d{2}/\d{2}/\d{4})', re.IGNORECASE)

    def extrair(self, conteudo_arquivo: bytes) -> dict:
        parcelas = []
        detalhes_contrato = {
            "cliente": "Não identificado",
            "cpf_cnpj": "",
            "loteamento": "",
            "quadra": "",
            "lote": "",
            "contrato": "",
            "periodicidade": "",
            "indexador": "",
            "juros_percentual": "",
            "data_emissao": "",
            "data_base_correcao": "",
        }

        hoje = datetime.now().date()

        with pdfplumber.open(io.BytesIO(conteudo_arquivo)) as pdf:
            for pagina in pdf.pages:
                texto = pagina.extract_text()
                if not texto:
                    continue

                self._extrair_detalhes_contrato(texto, detalhes_contrato)

                for linha in texto.split("\n"):
                    m = self.PADRAO_LINHA_PARCELA.match(linha.strip())
                    if not m:
                        continue

                    data_vencimento = m.group("vencimento")
                    try:
                        vencimento_date = datetime.strptime(data_vencimento, "%d/%m/%Y").date()
                    except ValueError:
                        continue

                    dt_receb_str = m.group("dt_receb")
                    if dt_receb_str:
                        data_pagamento = dt_receb_str
                        status_parcela = "Pago"
                    else:
                        data_pagamento = None
                        status_parcela = "Vencido" if vencimento_date < hoje else "A vencer"

                    parcelas.append({
                        "parcela": m.group("parcela"),
                        "tipo": m.group("tipo").strip(),
                        "condicao": m.group("condicao") or "",
                        "renegociado": m.group("renegociado"),
                        "vencimento": data_vencimento,
                        "valor_parcela": self._parse_valor(m.group("valor_original")),
                        "acrescimos": self._parse_valor(m.group("acrescimos")),
                        "valor_original": self._parse_valor(m.group("valor_com_acrescimos")),
                        "dias_atraso": int(m.group("dias_atraso")),
                        "status": status_parcela,
                        "data_pagamento": data_pagamento,
                        "valor_baixa": self._parse_valor(m.group("valor_baixa")),
                    })

        if not parcelas:
            return {
                "erro": (
                    "Não foi possível localizar a tabela de parcelas no PDF: "
                    "nenhuma linha no layout esperado da REAL foi encontrada."
                )
            }

        return {"contrato": detalhes_contrato, "parcelas": parcelas, "cliente": detalhes_contrato["cliente"]}

    def _extrair_detalhes_contrato(self, texto, detalhes_contrato):
        m = self.PADRAO_CLIENTE.search(texto)
        if m:
            detalhes_contrato["cliente"] = m.group(1).strip()

        m = self.PADRAO_CPF_CNPJ.search(texto)
        if m:
            detalhes_contrato["cpf_cnpj"] = m.group(1).strip()

        m = self.PADRAO_LOTEAMENTO.search(texto)
        if m:
            detalhes_contrato["loteamento"] = m.group(1).strip()

        m = self.PADRAO_QUADRA.search(texto)
        if m:
            detalhes_contrato["quadra"] = m.group(1).strip()

        m = self.PADRAO_LOTE.search(texto)
        if m:
            detalhes_contrato["lote"] = m.group(1).strip()

        m = self.PADRAO_CONTRATO.search(texto)
        if m:
            detalhes_contrato["contrato"] = m.group(1).strip()

        m = self.PADRAO_PERIODICIDADE.search(texto)
        if m:
            detalhes_contrato["periodicidade"] = m.group(1).strip()

        m = self.PADRAO_INDEXADOR.search(texto)
        if m:
            detalhes_contrato["indexador"] = m.group(1).strip()

        m = self.PADRAO_JUROS.search(texto)
        if m:
            detalhes_contrato["juros_percentual"] = m.group(1).strip()

        m = self.PADRAO_DATA_EMISSAO.search(texto)
        if m:
            detalhes_contrato["data_emissao"] = m.group(1).strip()

        m = self.PADRAO_DATA_BASE.search(texto)
        if m:
            detalhes_contrato["data_base_correcao"] = m.group(1).strip()

    @staticmethod
    def _parse_valor(valor_str) -> float:
        """Converte string monetária no formato brasileiro (1.234,56) para float."""
        try:
            valor_limpo = valor_str.replace(".", "").replace(",", ".")
            return float(valor_limpo)
        except (ValueError, AttributeError, TypeError):
            return 0.0
