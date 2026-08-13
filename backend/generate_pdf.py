from fpdf import FPDF

class PDF(FPDF):
    def header(self):
        self.set_font('Arial', 'B', 15)
        self.cell(0, 10, 'FundedNext 25K 2-Step Drawdown Requirements', 0, 1, 'C')
        self.ln(10)

    def chapter_title(self, title):
        self.set_font('Arial', 'B', 12)
        self.cell(0, 10, title, 0, 1, 'L')
        self.ln(4)

    def chapter_body(self, body):
        self.set_font('Arial', '', 11)
        # Using multi_cell to handle line breaks
        self.multi_cell(0, 7, body)
        self.ln()

pdf = PDF()
pdf.add_page()
pdf.set_auto_page_break(auto=True, margin=15)

text_intro = """For a FundedNext $25,000 2-Step Account (specifically the Stellar or Evaluation models), the drawdown rules are straightforward and trader-friendly. They use a Balance-Based Daily Limit and a Static Overall Limit.

Here are the exact requirements explained with examples:"""

pdf.chapter_body(text_intro)

pdf.chapter_title("1. Maximum Daily Loss Limit: 5% ($1,250)")
text_daily = """You are permitted to lose a maximum of 5% of your initial account balance in a single day. For a $25k account, this means your maximum daily loss is permanently fixed at $1,250.

This limit resets every day at midnight (server time) and is calculated based on your starting balance for that day.

- Example 1 (Starting flat): You start Monday with your initial $25,000. Your daily loss limit is $1,250. If your equity (including open, floating trades) drops below $23,750 at any point during Monday, you fail.

- Example 2 (In profit): You start Tuesday with a balance of $26,000. Your daily loss limit is still exactly $1,250. Therefore, your equity cannot drop below $24,750 ($26,000 - $1,250) at any point on Tuesday.

- Example 3 (Intraday profit): You start Wednesday at $25,000. Your limit is $23,750. During the morning, you close a trade for $500 profit (Balance is now $25,500). Your daily floor remains at $23,750. This means for the rest of Wednesday, you can now afford to lose $1,750 before breaching!"""

pdf.chapter_body(text_daily)

pdf.chapter_title("2. Maximum Overall Loss Limit: 10% ($2,500)")
text_overall = """Your total, all-time loss cannot exceed 10% of your initial account balance. For a $25k account, your account equity must never drop below $22,500.

Because FundedNext uses a Static overall drawdown for these accounts, this $22,500 floor never moves up. It does not trail your profits!

- Example 1: You start the challenge and take a few losses. Your balance drops to $23,000. You are still alive because you haven't hit the $22,500 hard floor.

- Example 2 (The Static Advantage): You have a great week and grow your account from $25,000 to $28,000. Because the drawdown is static, your absolute floor remains locked at $22,500. You now have a massive $5,500 buffer (or 22% of your initial balance) to play with! You only have to worry about the $1,250 Daily Limit at this point."""

pdf.chapter_body(text_overall)

pdf.chapter_title("3. Why Your System is Perfect for This")
text_system = """As discussed, your AlgoEdge backend CircuitBreaker currently enforces a 3% Daily Limit, which automatically halts trading for the day if you are down $750. Because FundedNext allows you $1,250 a day, your system is heavily insulated against daily breaches.

Additionally, because your frontend displays the strict Peak-to-Trough drawdown (e.g., the 11.2% we looked at earlier), it means that you are evaluating your strategies against the harshest possible standard. If a strategy survives your backtester's 11.2% Peak-to-Trough drop, it will breeze through FundedNext's generous Static Overall Drawdown rules."""

pdf.chapter_body(text_system)

# Output PDF to the user's workspace
output_path = r"c:\Users\ikchr\Documents\AlgoEdge\FundedNext_25K_Drawdown_Explanation.pdf"
pdf.output(output_path)
print(f"PDF successfully generated at {output_path}")
