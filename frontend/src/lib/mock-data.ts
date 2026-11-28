import {
  AcademicProgram,
  CandidateDossier,
  CatalogProgramItem,
  ChatMessage,
  KnowledgeDocumentItem,
} from '@/types';

/**
 * Base academic programs for IIT & ISB (+ ISA extension)
 * Grounded on official NAU institutions (IIT, ISB, ISA Architecture).
 */
export const MOCK_PROGRAMS: AcademicProgram[] = [
  {
    code: 'IIT-ING-GLID',
    title: 'Cycle Ingénieur — Génie Logiciel & Informatique Décisionnelle',
    school: 'IIT',
    diplomaLevel: 'Ingénieur',
    description:
      'Formation d’ingénieurs en architectures logicielles, systèmes distribués, DevOps et informatique décisionnelle.',
    officialSource: 'https://iit.tn/filieres-ingenieur/genie-informatique-2/',
  },
  {
    code: 'IIT-ING-CIVIL',
    title: 'Cycle Ingénieur — Génie Civil',
    school: 'IIT',
    diplomaLevel: 'Ingénieur',
    description:
      'Calcul des structures, géotechnique, conduite de projets BTP et modélisation BIM.',
    officialSource: 'https://iit.tn/filieres-ingenieur/genie-civil/',
  },
  {
    code: 'ISB-LIC-BUSINESS',
    title: 'Licence en Administration des Affaires',
    school: 'ISB',
    diplomaLevel: 'Licence',
    description:
      'Management international, finance, marketing digital et stratégie d’entreprise.',
    officialSource: 'Catalogue Officiel NAU — ISB',
  },
  {
    code: 'ISA-ARCHI-1',
    title: 'Diplôme du Premier Cycle d’Études en Architecture',
    school: 'ISA',
    diplomaLevel: 'Licence',
    description:
      'Initiation au projet architectural, théorie de l’espace, géométrie et histoire de l’architecture.',
    officialSource: 'https://iit.tn/architecture-2/',
  },
];

/**
 * Offline fallback catalog entries for the Responsable catalog and RAG corpus workspace
 */
export const MOCK_CATALOG_PROGRAMS: CatalogProgramItem[] = [
  {
    id: '00000000-0000-4000-8000-000000000101',
    code: 'IIT-ING-GLID',
    school_code: 'iit',
    title: 'Cycle Ingénieur — Génie Logiciel & Informatique Décisionnelle',
    degree_level: 'cycle_ingenieur',
    degree_title: 'Ingénieur en Génie Logiciel & Informatique Décisionnelle (GLID)',
    duration_years: 3,
    study_modes: ['PRESENTIEL'],
    description:
      'Conception logicielle avancée, architectures microservices, DevOps et Business Intelligence.',
    source_document: 'https://iit.tn/filieres-ingenieur/genie-informatique-2/',
    is_active: true,
  },
  {
    id: '00000000-0000-4000-8000-000000000102',
    code: 'IIT-ING-DSIA',
    school_code: 'iit',
    title: 'Cycle Ingénieur — Data Science & Intelligence Artificielle',
    degree_level: 'cycle_ingenieur',
    degree_title: 'Ingénieur en Data Science & Intelligence Artificielle (DSIA)',
    duration_years: 3,
    study_modes: ['PRESENTIEL'],
    description:
      'Machine Learning, Deep Learning, traitement automatique du langage naturel (NLP) et Big Data.',
    source_document: 'https://iit.tn/filieres-ingenieur/genie-informatique-2/',
    is_active: true,
  },
  {
    id: '00000000-0000-4000-8000-000000000103',
    code: 'IIT-PREPA',
    school_code: 'iit',
    title: 'Classes Préparatoires Scientifiques & Technologiques',
    degree_level: 'prepa',
    degree_title: 'Cycle Préparatoire IIT',
    duration_years: 2,
    study_modes: ['PRESENTIEL'],
    description:
      'Formation intensive en mathématiques, physique et algorithmique préparant au cycle ingénieur.',
    source_document: 'https://iit.tn/prepa/',
    is_active: true,
  },
  {
    id: '00000000-0000-4000-8000-000000000104',
    code: 'ISB-LIC-BUSINESS',
    school_code: 'isb',
    title: 'Licence Administration des Affaires',
    degree_level: 'licence',
    degree_title: 'Licence en Sciences de Gestion — Administration des Affaires',
    duration_years: 3,
    study_modes: ['PRESENTIEL'],
    description: 'Management international, entrepreneuriat et pilotage des organisations.',
    source_document: 'Brochure Officielle ISB',
    is_active: true,
  },
  {
    id: '00000000-0000-4000-8000-000000000105',
    code: 'ISA-ARCHI-1',
    school_code: 'isa',
    title: 'Premier Cycle d’Études en Architecture',
    degree_level: 'licence',
    degree_title: 'Diplôme du Premier Cycle en Architecture',
    duration_years: 3,
    study_modes: ['PRESENTIEL'],
    description: 'Conception architecturale, urbanisme durable et représentation spatiale.',
    source_document: 'https://iit.tn/architecture-2/',
    is_active: true,
  },
];

/**
 * Offline fallback official RAG corpus documents for the Responsable workspace
 */
export const MOCK_KNOWLEDGE_DOCUMENTS: KnowledgeDocumentItem[] = [
  {
    id: '00000000-0000-4000-8000-000000000201',
    code: 'IIT-WEB-FORMATION',
    title: 'Portail Officiel des Formations IIT Sfax (iit.tn)',
    description: 'Synthèse officielle des parcours Prépa, Licences, Cycle Ingénieur et Mastères.',
    school_code: 'iit',
    doc_type: 'programme',
    status: 'PUBLISHED',
    is_published: true,
    index_status: 'PENDING',
    latest_version_number: 1,
    updated_at: '2026-11-27T10:00:00Z',
  },
  {
    id: '00000000-0000-4000-8000-000000000202',
    code: 'IIT-WEB-INSCRIPTION',
    title: 'Procédure & Modalités d’Inscription IIT (iit.tn)',
    description: 'Pièces constitutives du dossier de candidature et étapes administratives.',
    school_code: 'iit',
    doc_type: 'admission_rules',
    status: 'PUBLISHED',
    is_published: true,
    index_status: 'PENDING',
    latest_version_number: 1,
    updated_at: '2026-11-27T10:05:00Z',
  },
  {
    id: '00000000-0000-4000-8000-000000000203',
    code: 'ISB-BROCHURE-2026',
    title: 'Guide des Licences & Masters International School of Business (ISB)',
    description: 'Filières Finance, Comptabilité, Marketing Digital et Management.',
    school_code: 'isb',
    doc_type: 'general_brochure',
    status: 'PUBLISHED',
    is_published: true,
    index_status: 'SYNCED',
    latest_version_number: 1,
    updated_at: '2026-11-25T14:30:00Z',
  },
];

/**
 * Initial starter chat messages for the VISION front-office interface
 * Welcomes prospective students, graduates, AND parents/relatives in Visitor mode.
 */
export const INITIAL_MESSAGES: ChatMessage[] = [
  {
    id: 'msg-1',
    sender: 'vision',
    subAgent: '1.1_Listener',
    actorStage: 'VISITOR',
    candidateStatus: 'ANONYMOUS',
    content:
      'Bonjour et bienvenue sur SmartCampus AI ! Je suis VISION, votre superviseur d’orientation pour l’IIT et l’ISB (ainsi que l’extension ISA au sein du groupe NAU à Sfax).\n\nQue vous soyez futur bachelier, diplômé en recherche de passerelle ou parent d’élève, vous naviguez librement en mode Visiteur (sans compte). Posez vos questions en Français, en Derja tunisienne (تونسي) ou en Arabizi — l’authentification Candidat ne sera demandée qu’au moment de déposer un relevé officiel ou de préinscrire un dossier.',
    timestamp: '10:00',
    detectedDialect: 'French',
    recommendations: undefined,
  },
];

/**
 * Initial candidate dossiers for the NAU Responsable dashboard
 */
export const MOCK_DOSSIERS: CandidateDossier[] = [
  {
    id: 'dos-101',
    candidateNumber: 'NAU-2026-0042',
    fullName: 'Youssef Trabelsi',
    email: 'youssef.candidate@example.tn',
    phone: '+216 98 123 456',
    targetSchool: 'IIT',
    targetTrack: 'Cycle Ingénieur Génie Informatique',
    status: 'SCHEDULED',
    declaredOrientationScore: 88,
    releveOcrConfidence: 0.96,
    qcmScore: 8,
    interviewSlot: {
      date: '2026-11-20',
      startTime: '10:30',
      status: 'BOOKED',
      responsableName: 'M. Taoufik Ben Abdallah',
    },
    briefSummary:
      'Profil Bac Mathématiques avec forte appétence pour l’algorithmique et le développement. Relevé de notes officiel validé par VLM (confiance 96%). QCM technique complété avec score 8/10. Recommandation VISION : profil cohérent pour la filière Génie Informatique IIT.',
    submittedAt: '2026-11-14T09:15:00Z',
  },
  {
    id: 'dos-102',
    candidateNumber: 'NAU-2026-0043',
    fullName: 'Sarra Mansour',
    email: 'sarra.m@example.tn',
    phone: '+216 22 987 654',
    targetSchool: 'ISB',
    targetTrack: 'Licence Business Administration',
    status: 'PREINSCRIBED',
    declaredOrientationScore: 81,
    releveOcrConfidence: 0.91,
    qcmScore: 7,
    briefSummary:
      'Profil Bac Économie & Gestion. Intérêt marqué pour la finance d’entreprise et l’entrepreneuriat international. Relevé vérifié. Entretien en attente de programmation via JARVIS.',
    submittedAt: '2026-11-14T14:30:00Z',
  },
  {
    id: 'dos-103',
    candidateNumber: 'NAU-2026-0044',
    fullName: 'Ahmed Khemir',
    email: 'ahmed.k@example.tn',
    phone: '+216 55 443 322',
    targetSchool: 'IIT',
    targetTrack: 'Cycle Ingénieur Génie Civil',
    status: 'INTERVIEWED',
    declaredOrientationScore: 76,
    releveOcrConfidence: 0.93,
    qcmScore: 7,
    interviewSlot: {
      date: '2026-11-15',
      startTime: '14:00',
      status: 'ATTENDED',
      responsableName: 'Dr. Responsable IIT',
    },
    briefSummary:
      'Entretien passé le 15/11. Motivation confirmée pour le génie civil et les projets d’infrastructures durables. En attente de la décision finale signée du Responsable.',
    submittedAt: '2026-11-12T11:00:00Z',
  },
];
