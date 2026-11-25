using UnityEngine;

public class Enemy : MonoBehaviour
{
    [SerializeField] private int _damage = 10;

    private void OnTriggerEnter(Collider other)
    {
        if (other.TryGetComponent(out Player player))
            player.Damage(_damage);
    }
}
