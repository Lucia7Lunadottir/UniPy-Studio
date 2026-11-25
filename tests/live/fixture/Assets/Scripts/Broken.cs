using UnityEngine;

public class Broken : MonoBehaviour
{
    private void Start()
    {
        int count = "text";                 // expect: CS0029
        NoSuchMethod();                     // expect: CS0103
        transform.NoSuchMember();           // expect: CS1061
        UnknownType value = null;           // expect: CS0246
        int unused;                         // expect: CS0168
        Debug.Log("missing semicolon")      // expect: CS1002
        Debug.Log(count + value.ToString());
    }

    private int NoReturn()                  // expect: CS0161
    {
    }
}
