from odoo import api, fields, models
from odoo.tools.translate import _


class AccountMoveTemplate(models.Model):
    _name = "account.move.template"
    _description = "Journal Entry Template"
    _check_company_auto = True

    name = fields.Char(required=True, index=True, translate=True)
    active = fields.Boolean(default=True)
    company_id = fields.Many2one(
        comodel_name="res.company",
        string="Company",
        default=lambda self: self.env.company,
    )
    journal_id = fields.Many2one(
        comodel_name="account.journal",
        string="Journal",
        check_company=True,
        readonly=False,
        store=False,
    )
    journal_code = fields.Char(
        string="Journal Code",
        required=True,
        help="When creating a new journal entry a journal having this code"
        "will be looked for",
    )
    partner_id = fields.Many2one(
        comodel_name="res.partner",
        string="Partner",
        check_company=True,
    )
    invoice_payment_term_id = fields.Many2one(
        comodel_name="account.payment.term",
        string="Payment Terms",
        help="Used to compute the due date of the journal item.",
    )
    move_type = fields.Selection(
        selection=[
            ("entry", "Journal Entry"),
            ("in_invoice", "Vendor Bill"),
            ("in_refund", "Vendor Credit Note"),
            ("out_invoice", "Customer Invoice"),
            ("out_refund", "Customer Credit Note"),
        ],
        default="entry",
        required=True,
        help="Type of journal entry this template will create",
    )
    ref = fields.Char(
        string="Reference",
        help="Internal reference or note",
    )
    line_ids = fields.One2many(
        comodel_name="account.move.template.line",
        inverse_name="template_id",
        string="Lines",
    )
    is_payment = fields.Boolean(
        string="Is Payment",
        help="If checked, this template will generate a payment instead of a journal entry",
        default=False,
    )
    payment_type = fields.Selection(
        selection=[("outbound", "Send Money"), ("inbound", "Receive Money")],
        string="Payment Type",
        help="Determines the flow of the payment (outbound or inbound)",
    )
    partner_type = fields.Selection(
        selection=[("customer", "Customer"), ("supplier", "Vendor")],
        string="Partner Type",
        help="Determines whether the payment is for a customer or vendor",
    )

    _sql_constraints = [
        (
            "name_company_unique",
            "unique(name, company_id)",
            "This name is already used by another template!",
        ),
    ]

    def copy(self, default=None):
        """Override to set a different name when copying a template"""
        self.ensure_one()
        default = dict(default or {})
        default.update(name=_("%s (copy)") % self.name)
        return super().copy(default)

    @api.onchange("journal_id")
    def _onchange_journal_id(self):
        if not self.journal_id:
            return

        self.journal_code = self.journal_id.code
        self.journal_id = False

    def action_move_template_run(self):
        """Open wizard to create move from template"""
        self.ensure_one()
        wizard = self.env["account.move.template.run"].create(
            {
                "template_id": self.id,
                "partner_id": self.partner_id.id,
                "date": fields.Date.context_today(self),
                "ref": self.ref,
            }
        )
        wizard.load_lines()
        return {
            "name": _("Create Entry from Template"),
            "type": "ir.actions.act_window",
            "res_model": "account.move.template.run",
            "view_mode": "form",
            "target": "new",
            "res_id": wizard.id,
        }

    def generate_journal_entry(self):
        self.ensure_one()

        context = self.env.context
        partner_id = context.get("default_partner_id") or self.partner_id.id
        date = context.get("default_date") or fields.Date.context_today(self)
        ref = context.get("default_ref") or self.ref
        amount = context.get("amount")  # si aplica en pagos automáticos
        overwrite = context.get("overwrite")

        wizard_vals = {
            "template_id": self.id,
            "partner_id": partner_id,
            "date": date,
            "ref": ref,
        }

        if amount and self.is_payment:
            wizard_vals["payment_amount"] = amount
        if overwrite:
            wizard_vals["overwrite"] = overwrite

        wizard = self.env["account.move.template.run"].create(wizard_vals)
        wizard.load_lines()

        return {
            "name": _("Create Entry from Template"),
            "type": "ir.actions.act_window",
            "res_model": "account.move.template.run",
            "view_mode": "form",
            "target": "new",
            "res_id": wizard.id,
        }
