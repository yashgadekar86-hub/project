from langchain_core.prompts import ChatPromptTemplate, MessagesPlaceholder

from tradingagents.agents.utils.agent_utils import (
    get_balance_sheet,
    get_cashflow,
    get_fundamentals,
    get_global_news,
    get_income_statement,
    get_instrument_context_from_state,
    get_language_instruction,
    get_macro_indicators,
)


def _commodity_fundamentals_analyst(llm):
    """Fundamentals node for commodities (gold, silver, oil...).

    Commodities have no company financials, so the usual balance-sheet /
    income-statement tools are replaced with the drivers that actually price
    the instrument: macro series via FRED (real yields, dollar, inflation,
    policy rates) and macro/global news for supply-demand and flows context.
    """

    def commodity_fundamentals_analyst_node(state):
        current_date = state["trade_date"]
        instrument_context = get_instrument_context_from_state(state)

        tools = [
            get_macro_indicators,
            get_global_news,
        ]

        system_message = (
            "You are a commodities strategist tasked with analyzing the fundamental "
            "drivers of a commodity over the past week. The target is a precious metal, "
            "energy, or other commodity future/spot instrument -- NOT a company -- so do "
            "not look for earnings, balance sheets, or company financials. Build a "
            "comprehensive fundamentals report from macro and supply/demand drivers:\n"
            "1. Real interest rates and nominal yields (e.g. fed_funds_rate, 10y_treasury, "
            "real_yield_10y / DFII10) -- the opportunity cost of holding non-yielding metals.\n"
            "2. The US dollar (dollar_index) and inflation prints (cpi, core_pce, "
            "inflation_expectations) -- the traditional inverse driver of gold.\n"
            "3. Central bank policy stance and macro growth/labor data that shift rate "
            "expectations (unemployment, payrolls, real_gdp).\n"
            "4. Supply/demand and flows context from recent macro news: central-bank gold "
            "buying, ETF flows, mine supply, refinery/energy inventories, geopolitics and "
            "safe-haven demand.\n"
            "Use `get_macro_indicators` for each relevant series (friendly alias or raw "
            "FRED ID) with the analysis date, and `get_global_news` for the flows and "
            "geopolitical backdrop. Anchor every claim to the numbers the tools return; "
            "never invent data. State clearly whether the current fundamental setup is "
            "supportive, neutral, or hostile for the commodity, and why. Provide specific, "
            "actionable insights with supporting evidence to help traders make informed "
            "decisions."
            + " Make sure to append a Markdown table at the end of the report to organize key points in the report, organized and easy to read."
            + get_language_instruction(),
        )

        prompt = ChatPromptTemplate.from_messages(
            [
                (
                    "system",
                    "You are a helpful AI assistant, collaborating with other assistants."
                    " Use the provided tools to progress towards answering the question."
                    " If you are unable to fully answer, that's OK; another assistant with different tools"
                    " will help where you left off. Execute what you can to make progress."
                    " If you or any other assistant has the FINAL TRANSACTION PROPOSAL: **BUY/HOLD/SELL** or deliverable,"
                    " prefix your response with FINAL TRANSACTION PROPOSAL: **BUY/HOLD/SELL** so the team knows to stop."
                    " You have access to the following tools: {tool_names}."
                    " Today's date is {current_date}; treat it as 'now' for all analysis and tool-call date ranges. {instrument_context}\n"
                    "{system_message}",
                ),
                MessagesPlaceholder(variable_name="messages"),
            ]
        )

        prompt = prompt.partial(system_message=system_message)
        prompt = prompt.partial(tool_names=", ".join([tool.name for tool in tools]))
        prompt = prompt.partial(current_date=current_date)
        prompt = prompt.partial(instrument_context=instrument_context)

        chain = prompt | llm.bind_tools(tools)

        result = chain.invoke(state["messages"])

        report = ""

        if len(result.tool_calls) == 0:
            report = result.content

        return {
            "messages": [result],
            "fundamentals_report": report,
        }

    return commodity_fundamentals_analyst_node


def create_fundamentals_analyst(llm):
    stock_node = _stock_fundamentals_analyst(llm)
    commodity_node = _commodity_fundamentals_analyst(llm)

    def fundamentals_analyst_node(state):
        if state.get("asset_type", "stock") == "commodity":
            return commodity_node(state)
        return stock_node(state)

    return fundamentals_analyst_node


def _stock_fundamentals_analyst(llm):
    def fundamentals_analyst_node(state):
        current_date = state["trade_date"]
        instrument_context = get_instrument_context_from_state(state)

        tools = [
            get_fundamentals,
            get_balance_sheet,
            get_cashflow,
            get_income_statement,
        ]

        system_message = (
            "You are a researcher tasked with analyzing fundamental information over the past week about a company. Please write a comprehensive report of the company's fundamental information such as financial documents, company profile, basic company financials, and company financial history to gain a full view of the company's fundamental information to inform traders. Make sure to include as much detail as possible. Provide specific, actionable insights with supporting evidence to help traders make informed decisions."
            + " Make sure to append a Markdown table at the end of the report to organize key points in the report, organized and easy to read."
            + " Use the available tools: `get_fundamentals` for comprehensive company analysis, `get_balance_sheet`, `get_cashflow`, and `get_income_statement` for specific financial statements."
            + get_language_instruction(),
        )

        prompt = ChatPromptTemplate.from_messages(
            [
                (
                    "system",
                    "You are a helpful AI assistant, collaborating with other assistants."
                    " Use the provided tools to progress towards answering the question."
                    " If you are unable to fully answer, that's OK; another assistant with different tools"
                    " will help where you left off. Execute what you can to make progress."
                    " If you or any other assistant has the FINAL TRANSACTION PROPOSAL: **BUY/HOLD/SELL** or deliverable,"
                    " prefix your response with FINAL TRANSACTION PROPOSAL: **BUY/HOLD/SELL** so the team knows to stop."
                    " You have access to the following tools: {tool_names}."
                    " Today's date is {current_date}; treat it as 'now' for all analysis and tool-call date ranges. {instrument_context}\n"
                    "{system_message}",
                ),
                MessagesPlaceholder(variable_name="messages"),
            ]
        )

        prompt = prompt.partial(system_message=system_message)
        prompt = prompt.partial(tool_names=", ".join([tool.name for tool in tools]))
        prompt = prompt.partial(current_date=current_date)
        prompt = prompt.partial(instrument_context=instrument_context)

        chain = prompt | llm.bind_tools(tools)

        result = chain.invoke(state["messages"])

        report = ""

        if len(result.tool_calls) == 0:
            report = result.content

        return {
            "messages": [result],
            "fundamentals_report": report,
        }

    return fundamentals_analyst_node
