import pdfplumber
import io
import re
from datetime import datetime
from app.extractors.base import ExtratorBase


class ExtratorViverBem(ExtratorBase):

    # Ordem das colunas extras no layout "completo"
    COLUNAS_COMPLETO = [
        "juros", "correcao", "multa", "juros_atraso",
        "acrescimo", "desconto_antecipado", "outros"
    ]

    # Ordem das colunas extras no layout "reduzido"
    COLUNAS_REDUZIDO = ["correcao", "multa", "juros_atraso"]

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
        padrao_data = re.compile(r'^\d{2}/\d{2}/\d{4}$')

        # Detecta qual layout de colunas extras o relatório usa.
        # Só precisa ser feito uma vez (o layout é o mesmo em todas as páginas do PDF).
        padrao_layout_completo = re.compile(
            r'Receb\.?\s*Juros\s*Correç[ãa]o', re.IGNORECASE
        )

        colunas_extras = None  # será definido assim que encontrarmos o cabeçalho
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

                # Define o layout de colunas extras assim que o cabeçalho aparecer
                if colunas_extras is None and ("Dt. Receb." in texto or "Dt.Receb." in texto):
                    if padrao_layout_completo.search(texto):
                        colunas_extras = self.COLUNAS_COMPLETO
                    else:
                        colunas_extras = self.COLUNAS_REDUZIDO

                linhas = texto.split("\n")
                for linha in linhas:
                    linha = linha.strip()
                    if padrao_linha_parcela.match(linha):
                        partes = linha.split()
                        try:
                            codigo_parcela = partes[0]
                            data_vencimento = partes[2]
                            valor_original_str = partes[3]

                            data_pagamento = None
                            idx_resto = 4  # onde começam as colunas extras, por padrão

                            if len(partes) > 4 and padrao_data.match(partes[4]):
                                data_pagamento = partes[4]
                                status_parcela = "Pago"
                                idx_resto = 5
                            else:
                                vencimento_date = datetime.strptime(data_vencimento, "%d/%m/%Y").date()
                                if vencimento_date < hoje:
                                    status_parcela = "Vencido"
                                else:
                                    status_parcela = "A vencer"

                            valor_original_float = self._parse_valor(valor_original_str)

                            # Layout usado para esta linha (fallback pro completo, caso
                            # o cabeçalho não tenha sido identificado ainda)
                            layout_atual = colunas_extras or self.COLUNAS_COMPLETO

                            resto = partes[idx_resto:]
                            extras = {}
                            for i, nome_coluna in enumerate(layout_atual):
                                if i < len(resto):
                                    extras[nome_coluna] = self._parse_valor(resto[i])
                                else:
                                    extras[nome_coluna] = 0.0

                            parcela = {
                                "parcela": codigo_parcela,
                                "vencimento": data_vencimento,
                                "valor_original": valor_original_float,
                                "status": status_parcela,
                                "data_pagamento": data_pagamento,
                            }
                            # Garante que todas as chaves do layout "completo" sempre existam,
                            # mesmo quando o PDF só trouxe o layout "reduzido"
                            for nome_coluna in self.COLUNAS_COMPLETO:
                                parcela[nome_coluna] = extras.get(nome_coluna, 0.0)

                            parcelas.append(parcela)
                        except (IndexError, ValueError):
                            continue

        return {"contrato": detalhes_contrato, "parcelas": parcelas, "cliente": detalhes_contrato["cliente"]}

    @staticmethod
    def _parse_valor(valor_str: str) -> float:
        """Converte string monetária no formato brasileiro (1.234,56) para float."""
        try:
            valor_limpo = valor_str.replace(".", "").replace(",", ".")
            return float(valor_limpo)
        except (ValueError, AttributeError):
            return 0.0