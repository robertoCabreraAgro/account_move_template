# models/account_move_template.py
from odoo import api, fields, models, _
from odoo.exceptions import UserError
from odoo.tools.safe_eval import safe_eval


class AccountMoveTemplate(models.Model):
    _name = "account.move.template"
    _description = "Journal Entry Template"
    _check_company_auto = True

    name = fields.Char(required=True, index=True)
    company_id = fields.Many2one(
        comodel_name="res.company",
        string="Company",
        default=lambda self: self.env.company,
        required=True,
    )
    # Nueva compañía destino para la creación de asientos
    target_company_id = fields.Many2one(
        comodel_name="res.company",
        string="Target Company",
        help="If set, journal entries will be created in this company, "
             "regardless of the workflow company or user's company",
        default=lambda self: self.env.company,
    )
    journal_id = fields.Many2one(
        comodel_name="account.journal",
        string="Journal",
        check_company=True,
        domain="[('company_id', '=', target_company_id)]",
    )
    ref = fields.Char(string="Reference", help="Internal reference or note")
    line_ids = fields.One2many(
        comodel_name="account.move.template.line", 
        inverse_name="template_id", 
        string="Lines",
    )
    active = fields.Boolean(default=True)
    move_type = fields.Selection(
        selection=[
            ("entry", "Journal Entry"),
            ("out_invoice", "Customer Invoice"),
            ("out_refund", "Customer Credit Note"),
            ("in_invoice", "Vendor Bill"),
            ("in_refund", "Vendor Credit Note"),
        ],
        default="entry",
        required=True,
        help="Type of journal entry this template will create",
    )
    partner_id = fields.Many2one(
        comodel_name="res.partner",
        string="Default Partner",
        check_company=True,
    )
    date = fields.Date(
        string="Default Date",
        help="Default date to use when creating entry from this template",
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
    
    @api.onchange('target_company_id')
    def _onchange_target_company_id(self):
        """Actualiza el diario cuando cambia la compañía destino"""
        for template in self:
            if template.target_company_id and (not template.journal_id or 
                                              template.journal_id.company_id != template.target_company_id):
                if template.move_type in ('out_invoice', 'out_refund'):
                    journal_type = 'sale'
                elif template.move_type in ('in_invoice', 'in_refund'):
                    journal_type = 'purchase'
                else:
                    journal_type = 'general'
                    
                domain = [('type', '=', journal_type), ('company_id', '=', template.target_company_id.id)]
                journal = self.env['account.journal'].search(domain, limit=1)
                if journal:
                    template.journal_id = journal.id
    
    @api.onchange('move_type')
    def _onchange_move_type(self):
        """Update journal based on move_type"""
        for template in self:
            if template.move_type in ('out_invoice', 'out_refund'):
                journal_type = 'sale'
            elif template.move_type in ('in_invoice', 'in_refund'):
                journal_type = 'purchase'
            else:
                journal_type = 'general'
                
            target_company = template.target_company_id or template.company_id
            domain = [('type', '=', journal_type), ('company_id', '=', target_company.id)]
            journal = self.env['account.journal'].search(domain, limit=1)
            if journal:
                template.journal_id = journal.id
    
    def action_move_template_run(self):
        """Open wizard to create move from template"""
        self.ensure_one()
        # Usar la compañía destino si está definida, sino usar la compañía del template
        target_company = self.target_company_id or self.company_id
        return {
            "type": "ir.actions.act_window",
            "name": _("Create Entry from Template"),
            "res_model": "account.move.template.run",
            "view_mode": "form",
            "target": "new",
            "context": {
                "default_template_id": self.id,
                "default_company_id": target_company.id,
                "default_journal_id": self.journal_id.id,
                "default_partner_id": self.partner_id.id,
                "default_move_type": self.move_type,
                "default_ref": self.ref,
                "default_date": self.date or fields.Date.context_today(self),
            },
        }
        
    def compute_lines(self, vals):
        """Compute the values of all computed line in the template"""
        for tmpl in self:
            computed_lines = tmpl.line_ids.filtered(lambda l: l.type == "computed")
            
            for line in sorted(computed_lines, key=lambda l: l.sequence):
                sequence = line.sequence
                eval_context = {key: vals[key] for key in vals}
                
                for seq in range(sequence):
                    seq_str = f"L{seq}"
                    if seq_str in eval_context:
                        continue
                    if seq in vals:
                        eval_context[seq_str] = vals[seq]
                
                try:
                    vals[sequence] = safe_eval(line.python_code, eval_context)
                except Exception as e:
                    if "unexpected EOF" in str(e):
                        raise UserError(_(
                            "Impossible to compute the formula for line with sequence %(sequence)s "
                            "(formula: %(code)s). Check that the lines used in the formula "
                            "exist and have a lower sequence than the current line.",
                            sequence=sequence,
                            code=line.python_code,
                        ))
                    else:
                        raise UserError(_(
                            "Syntax error in formula for line with sequence %(sequence)s "
                            "(formula: %(code)s).",
                            sequence=sequence,
                            code=line.python_code,
                        ))
        
        return vals