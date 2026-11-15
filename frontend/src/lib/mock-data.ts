import { AcademicProgram, CandidateDossier, ChatMessage } from '@/types';

/**
 * Verified base academic programs for IIT & ISB (+ ISA extension)
 */
export const MOCK_PROGRAMS: AcademicProgram[] = [
  {
    code: 'IIT-INFO-ING',
    title: 'Cycle d’Ingénieur en Génie Informatique',
    school: 'IIT',
    diplomaLevel: 'Ingénieur',
    description: 'Formation d’ingénieur d’excellence axée sur le génie logiciel, le Cloud, l’IA et la cybersécurité.',
    officialSource: 'Catalogue Officiel NAU 2026/2027 — IIT',
  },
  {
    code: 'IIT-GC-ING',
    title: 'Cycle d’Ingénieur en Génie Civil',
    school: 'IIT',
    diplomaLevel: 'Ingénieur',
    description: 'Conception structurelle, gestion de projets BTP et modélisation BIM.',
    officialSource: 'Catalogue Officiel NAU 2026/2027 — IIT',
  },
  {
    code: 'ISB-BA-LIC',
    title: 'Licence en Business Administration & Management',
    school: 'ISB',
    diplomaLevel: 'Licence',
    description: 'Management international, finance, marketing digital et stratégie d’entreprise.',
    officialSource: 'Catalogue Officiel NAU 2026/2027 — ISB',
  },
  {
    code: 'ISA-AGRO-ING',
    title: 'Ingénierie Agroalimentaire & Biotechnologies',
    school: 'ISA',
    diplomaLevel: 'Ingénieur',
    description: 'Industries agroalimentaires avancées, durabilité et sécurité alimentaire.',
    officialSource: 'Catalogue NAU 2026/2027 — Extension ISA',
  },
];

/**
 * Initial starter chat messages for the VISION front-office interface
 */
export const INITIAL_MESSAGES: ChatMessage[] = [
  {
    id: 'msg-1',
    sender: 'vision',
    subAgent: '1.1_Listener',
    content: 'Bonjour et bienvenue sur SmartCampus AI ! Je suis VISION, votre conseiller d’orientation intelligent pour l’IIT, l’ISB et l’ISA.\n\nVous pouvez me parler librement en Français, en Derja tunisienne (تونسي), ou en Arabizi (3aslema!). Comment puis-je vous guider aujourd’hui ?',
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
    briefSummary: 'Profil Bac Mathématiques avec forte appétence pour l’algorithmique et le développement. Relevé de notes officiel validé par OCR (confiance 96%). QCM technique complété avec score 8/10. Recommandation VISION : Excellent profil pour la filière Génie Informatique IIT.',
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
    briefSummary: 'Profil Bac Économie & Gestion. Intérêt marqué pour la finance d’entreprise et l’entrepreneuriat international. Relevé vérifié. Entretien en attente de programmation via JARVIS.',
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
      status: 'HELD',
      responsableName: 'Dr. Responsable IIT',
    },
    briefSummary: 'Entretien passé le 15/11. Motivation confirmée pour le génie civil et les projets d’infrastructures durables. En attente de la décision finale du Responsable.',
    submittedAt: '2026-11-12T11:00:00Z',
  },
];

