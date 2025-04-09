# models/account_move_template.py
from odoo import api, fields, models, _
from odoo.exceptions import UserError
from odoo.tools.safe_eval import safe_eval

class AccountMoveTemplate(models.Model):
    _name = "account.move.template"
    _description = "Journal Entry Template"

    name = fields.Char(required=True)
    company_id = fields.Many2one(
        'res.company',
        string='Company',
        default=lambda self: self.env.company,
        required=True
    )
    journal_id = fields.Many2one(
        'account.journal',
        string='Journal',
    )
    ref = fields.Char(string="Reference")
    line_ids = fields.One2many(
        "account.move.template.line", 
        "template_id", 
        string="Lines"
    )
    active = fields.Boolean(default=True)
    move_type = fields.Selection([
        ('entry', 'Journal Entry'),
        ('out_invoice', 'Customer Invoice'),
        ('out_refund', 'Customer Credit Note'),
        ('in_invoice', 'Vendor Bill'),
        ('in_refund', 'Vendor Credit Note'),
    ], default='entry', required=True)
    partner_id = fields.Many2one(
        'res.partner',
        string='Default Partner',
    )
    date = fields.Date(
        string='Default Date',
    )
    
    _sql_constraints = [
        (
            "name_company_unique",
            "unique(name, company_id)",
            "This name is already used by another template!",
        ),
    ]
    
    def copy(self, default=None):
        self.ensure_one()
        default = dict(default or {})
        default.update(name=_("%s (copy)") % self.name)
        return super().copy(default)
    
    def action_move_template_run(self):
        self.ensure_one()
        return {
            "type": "ir.actions.act_window",
            "name": _("Create Entry from Template"),
            "res_model": "account.move.template.run",
            "view_mode": "form",
            "target": "new",
            "context": {
                "default_template_id": self.id,
                "default_company_id": self.company_id.id,
            },
        }
        
    def compute_lines(self, vals):
        for tmpl in self:
            for line in tmpl.line_ids.filtered(lambda l: l.type == "computed"):
                sequence = line.sequence
                seq_ref = f"L{sequence}"
                eval_context = {key: vals[key] for key in vals}
                for seq in range(sequence):
                    seq_str = f"L{seq}"
                    if seq_str in eval_context:
                        continue
                    line_tmpl = tmpl.line_ids.filtered(lambda l: l.sequence == seq)
                    if line_tmpl:
                        eval_context[seq_str] = vals[seq]
                try:
                    vals[sequence] = safe_eval(line.python_code, eval_context)
                except Exception as e:
                    if "unexpected EOF" in str(e):
                        raise UserError(
                            _(
                                "Impossible to compute the formula of line with sequence %(sequence)s "
                                "(formula: %(code)s). Check that the lines used in the formula "
                                "really exists and have a lower sequence than the current line.",
                                sequence=sequence,
                                code=line.python_code,
                            )
                        )
                    else:
                        raise UserError(
                            _(
                                "Impossible to compute the formula of line with sequence %(sequence)s "
                                "(formula: %(code)s): the syntax of the formula is wrong.",
                                sequence=sequence,
                                code=line.python_code,
                            )
                        )