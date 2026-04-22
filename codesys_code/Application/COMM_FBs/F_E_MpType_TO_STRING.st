(*
FUNCTION F_E_MpType_TO_STRING
Description: Converts an E_MpType enumeration value to its string representation.
*)
FUNCTION F_E_MpType_TO_STRING : STRING
VAR_INPUT
	eType : E_MpType; // The enumeration value to convert
END_VAR
(* =========== IMPLEMENTATION =========== *)


	CASE eType OF
		E_MpType.MP_UNKNOWN:
			F_E_MpType_TO_STRING := 'UNKNOWN';
		E_MpType.MP_NIL:
			F_E_MpType_TO_STRING := 'NIL';
		E_MpType.MP_BOOL:
			F_E_MpType_TO_STRING := 'BOOL';
		E_MpType.MP_DINT:
			F_E_MpType_TO_STRING := 'DINT';
		E_MpType.MP_REAL:
			F_E_MpType_TO_STRING := 'REAL';
		E_MpType.MP_STRING:
			F_E_MpType_TO_STRING := 'STRING';
		E_MpType.MP_BINARY:
			F_E_MpType_TO_STRING := 'BINARY';
		E_MpType.MP_ARRAY:
			F_E_MpType_TO_STRING := 'ARRAY';
		E_MpType.MP_MAP:
			F_E_MpType_TO_STRING := 'MAP';
		ELSE
			F_E_MpType_TO_STRING := 'ERROR_INVALID_TYPE';
	END_CASE;