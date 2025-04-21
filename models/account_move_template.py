from odoo import api, fields, models, _
from odoo.exceptions import UserError
from odoo.tools.safe_eval import safe_eval
import logging

_logger = logging.getLogger(__name__)

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
    target_company_id = fields.Many2one(
        comodel_name="res.company",
        string="Target Company",
        default=lambda self: self.env.company,
        help="If set, journal entries will be created in this company.",
    )
    journal_id = fields.Many2one(
        comodel_name="account.journal",
        string="Journal",
        check_company=False,
    )
    # Campo para código de diario
    journal_code = fields.Char(
        string="Journal Code",
        help="Code of the journal to use for creating entries. If set, it will search for a journal with this code in the target company.",
    )
    ref = fields.Char(string="Reference")
    line_ids = fields.One2many(
        comodel_name="account.move.template.line", 
        inverse_name="template_id", 
        string="Lines",
    )
    active = fields.Boolean(default=True)
    move_type = fields.Selection([
        ("entry", "Journal Entry"),
        ("out_invoice", "Customer Invoice"),
        ("out_refund", "Customer Credit Note"),
        ("in_invoice", "Vendor Bill"),
        ("in_refund", "Vendor Credit Note"),
    ], default="entry", required=True)
    partner_id = fields.Many2one(
        comodel_name="res.partner",
        string="Default Partner",
        check_company=False,
    )
    date = fields.Date(
        string="Default Date",
        help="Default date to use when creating entry from this template",
    )

    @api.onchange('journal_code', 'target_company_id')
    def _onchange_journal_code(self):
        """Busca el diario que corresponde al código ingresado"""
        for template in self:
            if not template.journal_code or not template.target_company_id:
                template.journal_id = False
                continue
                
            journal = self.env['account.journal'].sudo().search([
                ('code', '=', template.journal_code),
                ('company_id', '=', template.target_company_id.id)
            ], limit=1)
            
            if journal:
                template.journal_id = journal.id
                _logger.info("Encontrado journal para código %s: %s (ID: %s)", 
                           template.journal_code, journal.name, journal.id)
            else:
                template.journal_id = False
                _logger.warning("No se encontró journal con código %s en la compañía %s", 
                              template.journal_code, template.target_company_id.name)

    @api.onchange('target_company_id', 'move_type')
    def _onchange_target_company_or_type(self):
        """Update journal suggestion based on move_type"""
        for template in self:
            if not template.target_company_id:
                continue

            journal_type = {
                'out_invoice': 'sale',
                'out_refund': 'sale',
                'in_invoice': 'purchase',
                'in_refund': 'purchase',
            }.get(template.move_type, 'general')
            
            # Buscar un diario del tipo correspondiente y mostrar su código como sugerencia
            journal = self.env['account.journal'].sudo().search([
                ('type', '=', journal_type),
                ('company_id', '=', template.target_company_id.id)
            ], limit=1)
            
            if journal and not template.journal_code:
                template.journal_code = journal.code
                _logger.info("Sugerencia de journal_code: %s para tipo %s", 
                           journal.code, journal_type)

    def action_create_move(self):
        """Create move directly from template"""
        self.ensure_one()
        
        # Determinar compañía destino
        target_company = self.target_company_id or self.company_id
        
        # Buscar el diario por código si está configurado
        journal = False
        if self.journal_code:
            # Buscar exactamente por el código proporcionado
            journal = self.env['account.journal'].sudo().search([
                ('code', '=ilike', self.journal_code),
                ('company_id', '=', target_company.id)
            ], limit=1)
            
            if not journal:
                # Si no se encontró, intentar con una búsqueda más flexible
                journal = self.env['account.journal'].sudo().search([
                    ('code', 'ilike', self.journal_code),
                    ('company_id', '=', target_company.id)
                ], limit=1)
                
                if not journal:
                    # Como último recurso, buscar por nombre similar
                    journal = self.env['account.journal'].sudo().search([
                        ('name', 'ilike', self.journal_code),
                        ('company_id', '=', target_company.id)
                    ], limit=1)
                
        # Si no se encontró journal por código, buscar uno del tipo apropiado
        if not journal:
            journal_type = {
                'out_invoice': 'sale',
                'out_refund': 'sale',
                'in_invoice': 'purchase',
                'in_refund': 'purchase',
            }.get(self.move_type, 'general')
            
            journal = self.env['account.journal'].sudo().search([
                ('type', '=', journal_type),
                ('company_id', '=', target_company.id)
            ], limit=1)
        
        if not journal:
            raise UserError(_("No journal found for code '%s' in company %s. Please configure a journal.") 
                            % (self.journal_code, target_company.name))
        
        # Preparar valores del asiento
        move_vals = {
            "ref": self.ref,
            "journal_id": journal.id,
            "date": self.date or fields.Date.context_today(self),
            "move_type": self.move_type,
            "company_id": target_company.id,
            "partner_id": self.partner_id.id if self.partner_id else False,
            "line_ids": [],
        }
        
        # Computar valores de líneas
        input_lines = self.line_ids.filtered(lambda l: l.type == "input")
        
        # Para simplificar, asumimos un monto fijo para las líneas de entrada
        amount = 1.0
        
        # Diccionario para almacenar los valores computados
        computed_values = {}
        for line in input_lines:
            computed_values[line.sequence] = amount
        
        # Computar valores de líneas calculadas
        computed_lines = self.line_ids.filtered(lambda l: l.type == "computed")
        for line in sorted(computed_lines, key=lambda l: l.sequence):
            sequence = line.sequence
            eval_context = {}
            
            for seq in range(sequence):
                seq_str = f"L{seq}"
                if seq in computed_values:
                    eval_context[seq_str] = computed_values[seq]
            
            try:
                if line.python_code and eval_context:
                    computed_values[sequence] = safe_eval(line.python_code, eval_context)
                else:
                    computed_values[sequence] = 0.0
            except Exception as e:
                raise UserError(_("Error evaluating formula for line %s: %s") % (sequence, str(e)))
        
        # Crear líneas de asiento
        for line in self.line_ids:
            amount = computed_values.get(line.sequence, 0.0)
            if not amount:
                continue
                
            # Buscar cuenta equivalente en la compañía destino si es necesario
            account_id = line.account_id.id
            
            # Verificar si la cuenta pertenece a la compañía destino
            is_account_in_company = False
            if hasattr(line.account_id, 'company_id'):
                is_account_in_company = line.account_id.company_id.id == target_company.id
            elif hasattr(line.account_id, 'company_ids'):
                is_account_in_company = target_company.id in line.account_id.company_ids.ids
                
            if not is_account_in_company:
                account = self.env['account.account'].sudo().search([
                    ('code', '=', line.account_id.code)
                ])
                
                # Buscar la cuenta que pertenece a la compañía destino
                account_found = False
                for acc in account:
                    if hasattr(acc, 'company_id'):
                        if acc.company_id.id == target_company.id:
                            account_id = acc.id
                            account_found = True
                            break
                    elif hasattr(acc, 'company_ids'):
                        if target_company.id in acc.company_ids.ids:
                            account_id = acc.id
                            account_found = True
                            break
                            
                if not account_found:
                    raise UserError(_("No equivalent account found in target company for account %s (%s)") 
                                    % (line.account_id.code, line.account_id.name))
            
            # Verificar si se debe usar cuenta alternativa para montos negativos
            debit = line.move_line_type == "dr"
            final_account_id = account_id
            
            if amount < 0 and line.opt_account_id:
                # Cambiar el signo y usar la cuenta alternativa
                debit = not debit
                amount = abs(amount)
                
                # Verificar si la cuenta opcional pertenece a la compañía destino
                is_opt_account_in_company = False
                if hasattr(line.opt_account_id, 'company_id'):
                    is_opt_account_in_company = line.opt_account_id.company_id.id == target_company.id
                elif hasattr(line.opt_account_id, 'company_ids'):
                    is_opt_account_in_company = target_company.id in line.opt_account_id.company_ids.ids
                    
                if not is_opt_account_in_company:
                    # Buscar cuenta equivalente
                    opt_accounts = self.env['account.account'].sudo().search([
                        ('code', '=', line.opt_account_id.code)
                    ])
                    
                    opt_account_found = False
                    for acc in opt_accounts:
                        if hasattr(acc, 'company_id'):
                            if acc.company_id.id == target_company.id:
                                final_account_id = acc.id
                                opt_account_found = True
                                break
                        elif hasattr(acc, 'company_ids'):
                            if target_company.id in acc.company_ids.ids:
                                final_account_id = acc.id
                                opt_account_found = True
                                break
                                
                    if not opt_account_found:
                        raise UserError(_("No equivalent optional account found in target company for %s") 
                                        % line.opt_account_id.name)
                else:
                    final_account_id = line.opt_account_id.id
            else:
                amount = abs(amount)
            
            # Preparar valores de la línea de asiento
            move_line_vals = {
                "name": line.name,
                "account_id": final_account_id,
                "partner_id": line.partner_id.id if line.partner_id else self.partner_id.id if self.partner_id else False,
                "debit": amount if debit else 0.0,
                "credit": amount if not debit else 0.0,
            }
            
            # Agregar impuestos si están configurados
            if line.tax_ids:
                # Buscar impuestos equivalentes en la compañía destino
                tax_ids = []
                for tax in line.tax_ids:
                    # Verificar si el impuesto pertenece a la compañía destino
                    is_tax_in_company = tax.company_id.id == target_company.id
                    
                    if not is_tax_in_company:
                        equiv_tax = self.env['account.tax'].sudo().search([
                            ('name', '=', tax.name),
                            ('amount', '=', tax.amount),
                            ('company_id', '=', target_company.id)
                        ], limit=1)
                        
                        if equiv_tax:
                            tax_ids.append(equiv_tax.id)
                    else:
                        tax_ids.append(tax.id)
                
                if tax_ids:
                    move_line_vals["tax_ids"] = [(6, 0, tax_ids)]
            
            move_vals["line_ids"].append((0, 0, move_line_vals))
        
        # Crear el asiento contable
        move = self.env['account.move'].sudo().with_company(target_company.id).create(move_vals)
        
        # Registrar información sobre el asiento creado
        _logger.info(
            "Created move %s in company %s using journal %s (code: %s)",
            move.name,
            target_company.name,
            journal.name,
            journal.code
        )
        
        # Devolver acción para mostrar el asiento creado
        return {
            'name': _('Journal Entry'),
            'type': 'ir.actions.act_window',
            'res_model': 'account.move',
            'view_mode': 'form',
            'res_id': move.id,
            'context': {'form_view_initial_mode': 'edit'},
        }
    def copy(self, default=None):
        """Override to set a different name when copying a template"""
        self.ensure_one()
        default = dict(default or {})
        default.update(name=_("%s (copy)") % self.name)
        return super().copy(default)