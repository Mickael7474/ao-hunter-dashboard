// ==UserScript==
// @name         Almera AutoFill - Plateformes Marches Publics
// @namespace    https://almera.one
// @version      1.0
// @description  Remplit automatiquement les formulaires des plateformes de depot avec le profil Almera (AI MENTOR / SASU). Ne soumet JAMAIS le formulaire.
// @author       Almera - AO Hunter
// @match        *://*.marches-publics.gouv.fr/*
// @match        *://marches-publics.gouv.fr/*
// @match        *://*.maximilien.fr/*
// @match        *://*.achatpublic.com/*
// @match        *://achatpublic.com/*
// @match        *://*.achats.defense.gouv.fr/*
// @match        *://achats.defense.gouv.fr/*
// @match        *://*.marches-securises.fr/*
// @match        *://marches-securises.fr/*
// @match        *://*.e-marchespublics.com/*
// @match        *://e-marchespublics.com/*
// @grant        none
// @run-at       document-idle
// ==/UserScript==

(function() {
    'use strict';

    // --- Profil Almera ---
    const PROFIL = {
        raison_sociale: 'AI MENTOR',
        nom_commercial: 'Almera',
        forme_juridique: 'SASU',
        siret: '98900455100010',
        siren: '989004551',
        nda: '11757431975',
        adresse: '25 rue Campagne Premiere',
        code_postal: '75014',
        ville: 'Paris',
        pays: 'France',
        code_pays: 'FR',
        representant: 'Mickael Bertolla',
        nom: 'Bertolla',
        prenom: 'Mickael',
        fonction: 'President',
        telephone: '0686680611',
        tel_international: '+33686680611',
        email: 'contact@almera.one',
        site_web: 'almera.one',
        url_site: 'https://almera.one',
        capital: '1000',
        capital_complet: '1000 EUR',
        code_ape: '8559A',
        code_naf: '8559A',
        tva_intra: 'FR98989004551',
        certifications: 'Qualiopi (formation), RS6776'
    };

    // --- Mapping champs ---
    const FIELD_MAPPINGS = [
        {
            value: PROFIL.raison_sociale,
            names: ['raison_sociale', 'raisonSociale', 'raison-sociale', 'company_name', 'companyName', 'company', 'denomination', 'denominationSociale', 'nom_societe', 'nomSociete', 'organisme', 'nom_organisme'],
            ids: ['raison_sociale', 'raisonSociale', 'company_name', 'companyName', 'denomination', 'organisme', 'nom_organisme', 'denominationSociale'],
            labels: ['raison sociale', 'denomination', 'denomination sociale', 'nom de la societe', 'nom de l\'entreprise', 'organisme', 'nom organisme', 'company name'],
            placeholders: ['raison sociale', 'denomination', 'nom de la societe', 'company name']
        },
        {
            value: PROFIL.nom_commercial,
            names: ['nom_commercial', 'nomCommercial', 'nom-commercial', 'trade_name', 'tradeName', 'enseigne'],
            ids: ['nom_commercial', 'nomCommercial', 'trade_name', 'enseigne'],
            labels: ['nom commercial', 'enseigne', 'trade name'],
            placeholders: ['nom commercial', 'enseigne']
        },
        {
            value: PROFIL.forme_juridique,
            names: ['forme_juridique', 'formeJuridique', 'forme-juridique', 'legal_form', 'legalForm', 'statut_juridique'],
            ids: ['forme_juridique', 'formeJuridique', 'legal_form', 'statut_juridique'],
            labels: ['forme juridique', 'statut juridique', 'legal form', 'type de societe'],
            placeholders: ['forme juridique', 'SASU, SAS, SARL']
        },
        {
            value: PROFIL.siret,
            names: ['siret', 'SIRET', 'no_siret', 'noSiret', 'num_siret', 'numSiret', 'numero_siret'],
            ids: ['siret', 'SIRET', 'no_siret', 'noSiret', 'num_siret', 'numero_siret'],
            labels: ['siret', 'n siret', 'numero siret', 'n° siret'],
            placeholders: ['siret', 'numero siret', '14 chiffres']
        },
        {
            value: PROFIL.siren,
            names: ['siren', 'SIREN', 'no_siren', 'num_siren'],
            ids: ['siren', 'SIREN', 'no_siren', 'num_siren'],
            labels: ['siren', 'n siren', 'numero siren', 'n° siren'],
            placeholders: ['siren', 'numero siren', '9 chiffres']
        },
        {
            value: PROFIL.nda,
            names: ['nda', 'NDA', 'num_declaration', 'numDeclaration', 'numero_declaration', 'declaration_activite', 'num_formation'],
            ids: ['nda', 'NDA', 'num_declaration', 'numero_declaration', 'declaration_activite'],
            labels: ['numero de declaration', 'declaration d\'activite', 'nda', 'n° declaration', 'numero declaration activite'],
            placeholders: ['numero de declaration', 'NDA', 'declaration activite']
        },
        {
            value: PROFIL.adresse,
            names: ['adresse', 'address', 'adresse1', 'adresse_1', 'adresse_rue', 'rue', 'street', 'address1', 'adresseLigne1', 'voie', 'adresse_voie', 'ligne1'],
            ids: ['adresse', 'address', 'adresse1', 'rue', 'street', 'address1', 'voie', 'ligne1', 'adresseLigne1'],
            labels: ['adresse', 'adresse postale', 'rue', 'voie', 'adresse ligne 1', 'adresse de l\'entreprise', 'adresse du siege', 'adresse siege social'],
            placeholders: ['adresse', 'rue', 'voie', 'adresse postale']
        },
        {
            value: PROFIL.code_postal,
            names: ['code_postal', 'codePostal', 'code-postal', 'cp', 'postal_code', 'postalCode', 'zip', 'zipcode', 'zip_code'],
            ids: ['code_postal', 'codePostal', 'cp', 'postal_code', 'postalCode', 'zip', 'zipcode'],
            labels: ['code postal', 'cp', 'postal code', 'zip'],
            placeholders: ['code postal', 'cp', '75000']
        },
        {
            value: PROFIL.ville,
            names: ['ville', 'city', 'commune', 'localite', 'town'],
            ids: ['ville', 'city', 'commune', 'localite'],
            labels: ['ville', 'commune', 'localite', 'city'],
            placeholders: ['ville', 'commune', 'Paris']
        },
        {
            value: PROFIL.pays,
            names: ['pays', 'country', 'nation'],
            ids: ['pays', 'country'],
            labels: ['pays', 'country'],
            placeholders: ['pays', 'country', 'France']
        },
        {
            value: PROFIL.representant,
            names: ['representant', 'dirigeant', 'mandataire', 'signataire', 'nom_representant', 'contact_name', 'contactName', 'nom_complet', 'fullname', 'full_name'],
            ids: ['representant', 'dirigeant', 'mandataire', 'signataire', 'nom_representant', 'contact_name', 'nom_complet'],
            labels: ['representant', 'representant legal', 'dirigeant', 'mandataire', 'signataire', 'nom du representant', 'nom complet', 'personne habilitee'],
            placeholders: ['representant', 'nom du dirigeant', 'nom complet']
        },
        {
            value: PROFIL.nom,
            names: ['nom', 'last_name', 'lastName', 'nom_famille', 'surname', 'family_name'],
            ids: ['nom', 'last_name', 'lastName', 'nom_famille', 'surname'],
            labels: ['nom', 'nom de famille', 'last name', 'surname'],
            placeholders: ['nom', 'nom de famille']
        },
        {
            value: PROFIL.prenom,
            names: ['prenom', 'first_name', 'firstName', 'given_name'],
            ids: ['prenom', 'first_name', 'firstName', 'given_name'],
            labels: ['prenom', 'first name', 'given name'],
            placeholders: ['prenom', 'first name']
        },
        {
            value: PROFIL.fonction,
            names: ['fonction', 'function', 'titre', 'title', 'poste', 'job_title', 'jobTitle', 'qualite', 'qualite_signataire'],
            ids: ['fonction', 'titre', 'poste', 'job_title', 'qualite', 'qualite_signataire'],
            labels: ['fonction', 'qualite', 'titre', 'poste', 'qualite du signataire', 'fonction du representant'],
            placeholders: ['fonction', 'president', 'directeur', 'qualite']
        },
        {
            value: PROFIL.telephone,
            names: ['telephone', 'tel', 'phone', 'tel_fixe', 'tel_mobile', 'mobile', 'numero_telephone', 'phone_number', 'phoneNumber', 'telContact'],
            ids: ['telephone', 'tel', 'phone', 'mobile', 'numero_telephone', 'phoneNumber'],
            labels: ['telephone', 'tel', 'tel.', 'numero de telephone', 'telephone fixe', 'telephone mobile', 'mobile', 'phone'],
            placeholders: ['telephone', '06...', '01...', 'numero de telephone', 'phone']
        },
        {
            value: PROFIL.email,
            names: ['email', 'mail', 'courriel', 'e-mail', 'emailContact', 'email_contact', 'adresse_email', 'adresseEmail', 'contact_email'],
            ids: ['email', 'mail', 'courriel', 'emailContact', 'adresse_email', 'contact_email'],
            labels: ['email', 'e-mail', 'courriel', 'adresse email', 'adresse e-mail', 'mail'],
            placeholders: ['email', 'e-mail', 'courriel', 'votre@email.com', 'contact@']
        },
        {
            value: PROFIL.url_site,
            names: ['site_web', 'siteWeb', 'site-web', 'website', 'url', 'site_internet', 'web'],
            ids: ['site_web', 'siteWeb', 'website', 'url', 'site_internet', 'web'],
            labels: ['site web', 'site internet', 'website', 'url', 'adresse web'],
            placeholders: ['site web', 'https://', 'www.', 'site internet']
        },
        {
            value: PROFIL.capital,
            names: ['capital', 'capital_social', 'capitalSocial', 'share_capital'],
            ids: ['capital', 'capital_social', 'capitalSocial'],
            labels: ['capital', 'capital social', 'montant du capital'],
            placeholders: ['capital', 'capital social', 'montant']
        },
        {
            value: PROFIL.code_ape,
            names: ['code_ape', 'codeAPE', 'ape', 'naf', 'code_naf', 'codeNAF', 'activite_principale'],
            ids: ['code_ape', 'codeAPE', 'ape', 'naf', 'code_naf', 'codeNAF'],
            labels: ['code ape', 'code naf', 'ape', 'naf', 'activite principale', 'code activite'],
            placeholders: ['code ape', 'code naf', '8559A']
        },
        {
            value: PROFIL.tva_intra,
            names: ['tva', 'tva_intra', 'tvaIntra', 'tva_intracommunautaire', 'vat', 'vat_number', 'vatNumber', 'numero_tva'],
            ids: ['tva', 'tva_intra', 'tvaIntra', 'tva_intracommunautaire', 'vat', 'numero_tva'],
            labels: ['tva', 'tva intracommunautaire', 'numero tva', 'n° tva', 'vat number', 'tva intra'],
            placeholders: ['tva', 'FR...', 'numero tva', 'tva intracommunautaire']
        }
    ];

    // --- Fonctions utilitaires ---

    function normalizeText(text) {
        if (!text) return '';
        return text.toLowerCase()
            .normalize('NFD').replace(/[\u0300-\u036f]/g, '')
            .replace(/[^a-z0-9\s]/g, ' ')
            .replace(/\s+/g, ' ')
            .trim();
    }

    function getLabelForField(field) {
        if (field.id) {
            const label = document.querySelector('label[for="' + field.id + '"]');
            if (label) return normalizeText(label.textContent);
        }
        const parentLabel = field.closest('label');
        if (parentLabel) return normalizeText(parentLabel.textContent);
        const prev = field.previousElementSibling;
        if (prev && prev.tagName === 'LABEL') return normalizeText(prev.textContent);
        return '';
    }

    function matchesAny(value, patterns) {
        if (!value) return false;
        const norm = normalizeText(value);
        return patterns.some(function(p) { return norm.includes(normalizeText(p)); });
    }

    function setFieldValue(field, value) {
        if (!field || field.disabled || field.readOnly) return false;
        field.value = value;
        field.dispatchEvent(new Event('input', { bubbles: true }));
        field.dispatchEvent(new Event('change', { bubbles: true }));
        field.dispatchEvent(new Event('blur', { bubbles: true }));
        return true;
    }

    function highlightField(field) {
        field.style.backgroundColor = '#ffffcc';
        field.style.border = '2px solid #f0c000';
        field.style.transition = 'background-color 0.3s, border 0.3s';
    }

    // --- Remplissage ---

    function remplirFormulaire() {
        const allFields = document.querySelectorAll('input[type="text"], input[type="email"], input[type="tel"], input[type="url"], input[type="number"], input:not([type]), textarea, select');
        let filled = 0;
        let total = allFields.length;

        for (const mapping of FIELD_MAPPINGS) {
            let matched = false;
            for (const field of allFields) {
                if (field.dataset.almeraFilled) continue;

                // Par name
                if (field.name && matchesAny(field.name, mapping.names)) {
                    if (setFieldValue(field, mapping.value)) {
                        highlightField(field);
                        field.dataset.almeraFilled = 'true';
                        filled++;
                        matched = true;
                        break;
                    }
                }
                // Par id
                if (!matched && field.id && matchesAny(field.id, mapping.ids)) {
                    if (setFieldValue(field, mapping.value)) {
                        highlightField(field);
                        field.dataset.almeraFilled = 'true';
                        filled++;
                        matched = true;
                        break;
                    }
                }
                // Par label
                if (!matched) {
                    const labelText = getLabelForField(field);
                    if (labelText && matchesAny(labelText, mapping.labels)) {
                        if (setFieldValue(field, mapping.value)) {
                            highlightField(field);
                            field.dataset.almeraFilled = 'true';
                            filled++;
                            matched = true;
                            break;
                        }
                    }
                }
                // Par placeholder
                if (!matched && field.placeholder && matchesAny(field.placeholder, mapping.placeholders)) {
                    if (setFieldValue(field, mapping.value)) {
                        highlightField(field);
                        field.dataset.almeraFilled = 'true';
                        filled++;
                        matched = true;
                        break;
                    }
                }
                if (matched) break;
            }
        }

        // Selects pays
        document.querySelectorAll('select').forEach(function(sel) {
            if (sel.dataset.almeraFilled) return;
            const label = getLabelForField(sel);
            const nameNorm = normalizeText(sel.name || '');
            if (nameNorm.includes('pays') || nameNorm.includes('country') ||
                (label && (label.includes('pays') || label.includes('country')))) {
                for (const opt of sel.options) {
                    const optText = normalizeText(opt.textContent);
                    if (optText === 'france' || opt.value === 'FR' || opt.value === 'FRA' || opt.value === 'France') {
                        sel.value = opt.value;
                        sel.dispatchEvent(new Event('change', { bubbles: true }));
                        highlightField(sel);
                        sel.dataset.almeraFilled = 'true';
                        filled++;
                        break;
                    }
                }
            }
        });

        return { filled: filled, total: total };
    }

    // --- UI : bouton flottant ---

    function createFloatingButton() {
        const btn = document.createElement('div');
        btn.id = 'almera-autofill-btn';
        btn.innerHTML = 'Remplir Almera';
        btn.style.cssText = 'position:fixed;bottom:20px;right:20px;background:#1a5276;color:white;padding:12px 20px;border-radius:25px;cursor:pointer;z-index:999999;font-family:sans-serif;font-size:14px;font-weight:bold;box-shadow:0 4px 12px rgba(0,0,0,0.3);user-select:none;transition:transform 0.2s,box-shadow 0.2s;';

        btn.addEventListener('mouseenter', function() {
            btn.style.transform = 'scale(1.05)';
            btn.style.boxShadow = '0 6px 16px rgba(0,0,0,0.4)';
        });
        btn.addEventListener('mouseleave', function() {
            btn.style.transform = 'scale(1)';
            btn.style.boxShadow = '0 4px 12px rgba(0,0,0,0.3)';
        });

        btn.addEventListener('click', function() {
            const result = remplirFormulaire();

            // Compteur
            let counter = document.getElementById('almera-autofill-counter');
            if (!counter) {
                counter = document.createElement('div');
                counter.id = 'almera-autofill-counter';
                counter.style.cssText = 'position:fixed;top:10px;right:10px;background:#1a5276;color:white;padding:12px 20px;border-radius:8px;z-index:999999;font-family:sans-serif;font-size:14px;box-shadow:0 4px 12px rgba(0,0,0,0.3);cursor:pointer;';
                counter.title = 'Cliquez pour fermer';
                counter.addEventListener('click', function() { counter.remove(); });
                document.body.appendChild(counter);
            }

            const plateforme = window.location.hostname;
            counter.innerHTML = '<strong>Almera AutoFill</strong><br>' +
                result.filled + ' champs remplis sur ' + result.total + ' detectes' +
                '<br><small>' + plateforme + '</small>' +
                '<br><small style="opacity:0.7">Cliquez pour fermer</small>';

            setTimeout(function() {
                if (counter.parentNode) {
                    counter.style.opacity = '0';
                    counter.style.transition = 'opacity 0.5s';
                    setTimeout(function() { counter.remove(); }, 500);
                }
            }, 10000);

            // Changer le texte du bouton temporairement
            btn.innerHTML = result.filled + ' champs remplis';
            btn.style.background = '#27ae60';
            setTimeout(function() {
                btn.innerHTML = 'Remplir Almera';
                btn.style.background = '#1a5276';
            }, 3000);
        });

        document.body.appendChild(btn);
    }

    // --- Lancement ---
    if (document.readyState === 'complete' || document.readyState === 'interactive') {
        createFloatingButton();
    } else {
        document.addEventListener('DOMContentLoaded', createFloatingButton);
    }

})();
