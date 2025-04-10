# models/account_move_template.py
from odoo import api, fields, models, _, Command
from odoo.exceptions import UserError
from odoo.tools.safe_eval import safe_eval
import logging

_logger = logging.getLogger(__name__)

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
                "default_journal_id":self.journal_id.id,
                "default_partner_id":self.partner_id.id,
                "default_move_type":self.move_type,
                "default_ref":self.ref,
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
    # Cambios requeridos en wizard/account_move_template_run.py

    def load_lines(self):
        self.ensure_one()
        overwrite_vals = self._get_overwrite_vals()
        amtlro = self.env["account.move.template.line.run"]
        template = self.template_id
        tmpl_lines = template.line_ids
        
        # Limpiar líneas existentes para evitar duplicados
        self.line_ids.unlink()

        # Crear líneas para todos los tipos (input y computed)
        for tmpl_line in tmpl_lines:
            vals = {
                "wizard_id": self.id,
                "sequence": tmpl_line.sequence,
                "name": tmpl_line.name,
                "amount": 0.0,
                "account_id": tmpl_line.account_id.id,
                "partner_id": tmpl_line.partner_id.id or False,
                "move_line_type": tmpl_line.move_line_type,
                "tax_ids": [(6, 0, tmpl_line.tax_ids.ids)],
                "note": tmpl_line.note,
                "payment_term_id": tmpl_line.payment_term_id.id if hasattr(tmpl_line, 'payment_term_id') else False,
                # Guardar el tipo para posteriormente calcular los valores
                "template_type": tmpl_line.type,
                "python_code": tmpl_line.python_code if tmpl_line.type == 'computed' else False,
            }
            
            # Si hay distribución analítica, añadirla
            if hasattr(tmpl_line, 'analytic_distribution') and tmpl_line.analytic_distribution:
                vals["analytic_distribution"] = tmpl_line.analytic_distribution
                
            amtlro.create(vals)

        journal = template.journal_id
        if not journal:
            domain = [('company_id', '=', self.company_id.id), ('type', '=', 'general')]
            journal = self.env['account.journal'].search(domain, limit=1)
            if not journal:
                raise UserError(_("No journal available for this template."))

        self.write({
            'journal_id': journal.id,
            "ref": template.ref,
            "partner_id": template.partner_id.id or self.partner_id.id,
            "date": template.date or self.date,
        })

        if not self.line_ids:
            return self.generate_move()

        # Aplicar sobreescritura a las líneas visibles
        self._overwrite_line(overwrite_vals)

        # Calcular valores para líneas computadas
        self._compute_line_values()

        result = self.env["ir.actions.actions"]._for_xml_id(
            "account_move_template.account_move_template_run_action"
        )
        result.update({"res_id": self.id, "context": self.env.context})

        # Limpiar 'amount' del contexto de sobreescritura para la próxima etapa
        for key in overwrite_vals.keys():
            overwrite_vals[key].pop("amount", None)

        result["context"] = dict(result.get("context", {}), overwrite=overwrite_vals)
        return result

    def _compute_line_values(self):
        """Calcular valores para líneas computadas basadas en líneas de entrada"""
        self.ensure_one()
        
        # Obtener valores de entrada
        sequence2amount = {}
        input_lines = self.line_ids.filtered(lambda l: l.template_type == 'input')
        for line in input_lines:
            sequence2amount[line.sequence] = line.amount
            
        # Calcular valores para líneas computadas
        computed_lines = self.line_ids.filtered(lambda l: l.template_type == 'computed')
        
        if not computed_lines:
            return
            
        # Necesitamos calcular en orden de secuencia
        for line in computed_lines.sorted(lambda l: l.sequence):
            try:
                sequence = line.sequence
                eval_context = {f"L{seq}": sequence2amount.get(seq, 0.0) 
                            for seq in sequence2amount.keys() if seq < sequence}
                
                if line.python_code and eval_context:
                    amount = safe_eval(line.python_code, eval_context)
                    line.amount = amount
                    sequence2amount[sequence] = amount
            except Exception as e:
                # Manejar excepciones elegantemente
                _logger.error("Error al calcular línea %s: %s", line.sequence, str(e))

    def generate_move(self):
        self.ensure_one()
        
        # Vamos a recalcular las líneas computadas para asegurar coherencia
        self._compute_line_values()
        
        # Mapeo de secuencia → monto ingresado por el usuario
        sequence2amount = {
            wizard_line.sequence: wizard_line.amount
            for wizard_line in self.line_ids
        }

        company_cur = self.company_id.currency_id

        move_vals = {
            "ref": self.ref,
            "journal_id": self.journal_id.id,
            "date": self.date,
            "company_id": self.company_id.id,
            "move_type": self.move_type,
            "line_ids": [],
        }
        
        # Si hay partner a nivel del wizard, usarlo
        if self.partner_id:
            move_vals["partner_id"] = self.partner_id.id

        for line in self.line_ids:
            amount = line.amount
            if not company_cur.is_zero(amount) and amount:
                move_vals["line_ids"].append(
                    Command.create(self._prepare_move_line(line, amount))
                )

        if not move_vals["line_ids"]:
            raise UserError(_("Debit and credit of all lines are null."))

        move = self.env["account.move"].create(move_vals)

        result = self.env["ir.actions.actions"]._for_xml_id(
            "account.action_move_journal_line"
        )
        result.update({
            "name": _("Entry from template %s") % self.template_id.name,
            "res_id": move.id,
            "views": False,
            "view_id": False,
            "view_mode": "form,list",
            "context": self.env.context,
        })
        return result

    def _prepare_move_line(self, line, amount):
        """Preparar valores para línea del asiento desde la línea del wizard"""
        date_maturity = False
        if hasattr(line, 'payment_term_id') and line.payment_term_id:
            pterm_list = line.payment_term_id.compute(value=1, date_ref=self.date)
            date_maturity = max(line[0] for line in pterm_list)
            
        debit = line.move_line_type == "dr"
        values = {
            "name": line.name,
            "account_id": line.account_id.id,
            "credit": not debit and abs(amount) or 0.0,
            "debit": debit and abs(amount) or 0.0,
            "partner_id": line.partner_id.id or self.partner_id.id,
            "date_maturity": date_maturity or self.date,
        }
        
        # Add optional fields if they exist
        if hasattr(line, 'tax_repartition_line_id') and line.tax_repartition_line_id:
            values["tax_repartition_line_id"] = line.tax_repartition_line_id.id
            
        if hasattr(line, 'analytic_distribution') and line.analytic_distribution:
            values["analytic_distribution"] = line.analytic_distribution
            
        if line.tax_ids:
            values["tax_ids"] = [Command.set(line.tax_ids.ids)]
            
            if hasattr(line, 'is_refund') and line.is_refund:
                tax_repartition = "refund_tax_id" if line.is_refund else "invoice_tax_id"
                atrl_ids = self.env["account.tax.repartition.line"].search(
                    [
                        (tax_repartition, "in", line.tax_ids.ids),
                        ("repartition_type", "=", "base"),
                    ]
                )
                if atrl_ids:
                    values["tax_tag_ids"] = [Command.set(atrl_ids.mapped("tag_ids").ids)]
                    
        # With overwrite options
        overwrite = self._context.get("overwrite", {})
        move_line_vals = overwrite.get(f"L{line.sequence}", {})
        values.update(move_line_vals)
        
        # Use optional account when amount is negative
        self._update_account_on_negative(line, values)
        return values