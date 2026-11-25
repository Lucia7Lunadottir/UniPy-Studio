using UnityEngine;

public class Boss : Player
{
    protected override void Die()
    {
        Debug.Log("Boss defeated");
        base.Die();
    }
}
