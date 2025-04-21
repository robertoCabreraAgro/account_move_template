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
