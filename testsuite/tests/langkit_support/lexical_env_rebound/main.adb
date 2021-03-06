--  Test that lookups on grouped lexical envs works correctly

with Ada.Exceptions;
with Ada.Text_IO; use Ada.Text_IO;

with Langkit_Support.Lexical_Env;
with Langkit_Support.Symbols; use Langkit_Support.Symbols;
with Langkit_Support.Text;    use Langkit_Support.Text;

with Support; use Support;
use Support.Envs;

procedure Main is
   Symbols : Symbol_Table := Create;
   Key_X   : constant Symbol_Type := Find (Symbols, "X");

   Old_Env : constant Lexical_Env :=
      Create (No_Env_Getter, 'O', Owner => True);
   New_Env : constant Lexical_Env :=
      Create (No_Env_Getter, 'N', Owner => True);

   Root  : constant Lexical_Env :=
      Create (No_Env_Getter, 'R', Owner => True);
   Child : constant Lexical_Env :=
      Create (Simple_Env_Getter (Root), 'R', Owner => True);

   Rebindings : constant Env_Rebindings := Append (null, Old_Env, New_Env);
   Rebound    : constant Lexical_Env := Rebind_Env (Child, Rebindings);
begin
   Add (Root, Key_X, '1');
   Add (Child, Key_X, '2');

   Put_Line ("Looking in Rebound:");
   Put_Line (Get (Rebound, Key_X));

   declare
      R : Env_Rebindings;
   begin
      R := Append (Rebindings, Old_Env, New_Env);
      Put_Line ("Double rebinding: no error raised...");
   exception
      when Exc : Property_Error =>
         Put_Line ("Got a Property_Error:");
         Put_Line (Ada.Exceptions.Exception_Message (Exc));
   end;
end Main;
